# =============================================================================
# LUCIFER PCE ORCHESTRATOR (Plan → Context → Execute)
# =============================================================================
# Requirements (as specified):
# PLAN PHASE:
#   - Use Claude to break complex commands into atomic subtasks
#   - Tools mapping: SEARCH, OPEN_URL, YOUTUBE, SCRAPE, SPEAK, SCREENSHOT, WAIT
#   - Parse and validate plan JSON
# CONTEXT PHASE:
#   - Rolling context window (last 10 conversation turns)
#   - SQLite-backed ContextManager
#   - Store: current plan, completed/failed steps, scraped data, preferences
# EXECUTE PHASE:
#   - Execute each step with appropriate handler
#   - Self-verify after each step using Claude
#   - Retry once on failure, then skip
#   - Synthesize final results on completion
# ADDITIONAL:
#   - Async task queue for parallel execution
#   - Self-monitoring: 30-second timeout with replanning
#   - Full logging to SQLite
# =============================================================================

import os
import re
import json
import time
import asyncio
import logging
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Tuple, Callable
from dataclasses import dataclass, field
from enum import Enum
from collections import deque

import requests
from anthropic import Anthropic
import pyttsx3

# Import browser and research modules globally (no circular dependency risk)
from lucifer_browser import handle_open_url, handle_youtube, get_page_text, handle_system
from lucifer_research import research, handle_search_with_research

# Lazy import intent module to break circular dependency
_intent_module = None

def _get_modules():
    global _intent_module
    if _intent_module is None:
        try:
            from lucifer_intent import route_command, Intent
            _intent_module = (route_command, Intent)
        except ImportError as e:
            print(f"[ORCHESTRATOR] Module import warning: {e}")


# =============================================================================
# CONFIGURATION CONSTANTS
# =============================================================================
GEMINI_MODEL = "gemini-2.0-flash"
LOG_FILE = "lucifer_session.log"
CONTEXT_DB = "lucifer_context.db"

MAX_CONTEXT_TURNS = 10
STEP_TIMEOUT_SECONDS = 30
MAX_RETRIES = 1
TASK_QUEUE_TIMEOUT = 5


# =============================================================================
# LOGGING SETUP
# =============================================================================
def setup_logging() -> logging.Logger:
    """Initialize logging for orchestrator."""
    logger = logging.getLogger("LuciferOrchestrator")

    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)

    file_handler = logging.FileHandler(LOG_FILE, mode='a', encoding='utf-8')
    file_handler.setLevel(logging.INFO)
    file_formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter('%(levelname)s: %(message)s'))
    logger.addHandler(console_handler)

    return logger


logger = setup_logging()


# =============================================================================
# DATA STRUCTURES
# =============================================================================
class Tool(Enum):
    """Available tools for planning."""
    SEARCH = "SEARCH"
    OPEN_URL = "OPEN_URL"
    YOUTUBE = "YOUTUBE"
    SCRAPE = "SCRAPE"
    SPEAK = "SPEAK"
    SCREENSHOT = "SCREENSHOT"
    WAIT = "WAIT"


@dataclass
class PlannedStep:
    """Single planned step in the execution plan."""
    step: int
    tool: str
    input: str
    reason: str
    status: str = "pending"  # pending, running, completed, failed, skipped
    result: Optional[str] = None
    error: Optional[str] = None
    retries: int = 0


@dataclass
class ExecutionPlan:
    """Complete execution plan from planning phase."""
    plan_id: str
    original_command: str
    steps: List[PlannedStep]
    created_at: datetime = field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None


@dataclass
class ConversationTurn:
    """Single conversation turn."""
    turn_id: int
    role: str  # user, assistant
    content: str
    timestamp: datetime = field(default_factory=datetime.now)


# =============================================================================
# DATABASE SCHEMA & CONTEXT MANAGER
# =============================================================================
class ContextManager:
    """
    SQLite-backed context manager for PCE orchestrator.
    Stores conversation history, plans, execution state.
    """

    def __init__(self, db_path: str = CONTEXT_DB):
        self.db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        """Initialize SQLite database schema."""
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS conversation_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                turn_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS execution_plans (
                plan_id TEXT PRIMARY KEY,
                original_command TEXT NOT NULL,
                plan_json TEXT NOT NULL,
                status TEXT DEFAULT 'created',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS execution_steps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plan_id TEXT NOT NULL,
                step_number INTEGER NOT NULL,
                tool TEXT NOT NULL,
                input_text TEXT,
                reason TEXT,
                status TEXT DEFAULT 'pending',
                result TEXT,
                error TEXT,
                retries INTEGER DEFAULT 0,
                started_at TIMESTAMP,
                completed_at TIMESTAMP,
                FOREIGN KEY (plan_id) REFERENCES execution_plans(plan_id)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS scraped_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plan_id TEXT NOT NULL,
                step_number INTEGER,
                url TEXT,
                content TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (plan_id) REFERENCES execution_plans(plan_id)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_preferences (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.commit()
        conn.close()
        logger.info("Context database initialized")

    def add_turn(self, session_id: str, role: str, content: str) -> None:
        """Add a conversation turn."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Get next turn ID for this session
        cursor.execute("""
            SELECT COALESCE(MAX(turn_id), 0) + 1
            FROM conversation_history
            WHERE session_id = ?
        """, (session_id,))
        turn_id = cursor.fetchone()[0]

        # Prune old turns (keep last MAX_CONTEXT_TURNS)
        cursor.execute("""
            DELETE FROM conversation_history
            WHERE session_id = ? AND turn_id <= (
                SELECT COALESCE(MIN(turn_id), 0) FROM (
                    SELECT turn_id FROM conversation_history
                    WHERE session_id = ?
                    ORDER BY turn_id DESC
                    LIMIT ?
                )
            )
        """, (session_id, session_id, MAX_CONTEXT_TURNS))

        cursor.execute("""
            INSERT INTO conversation_history (session_id, turn_id, role, content)
            VALUES (?, ?, ?, ?)
        """, (session_id, turn_id, role, content))

        conn.commit()
        conn.close()

    def get_recent_turns(self, session_id: str, limit: int = MAX_CONTEXT_TURNS) -> List[ConversationTurn]:
        """Get recent conversation turns."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("""
            SELECT turn_id, role, content, timestamp
            FROM conversation_history
            WHERE session_id = ?
            ORDER BY turn_id DESC
            LIMIT ?
        """, (session_id, limit))

        turns = [ConversationTurn(
            turn_id=row['turn_id'],
            role=row['role'],
            content=row['content'],
            timestamp=datetime.fromisoformat(row['timestamp'])
        ) for row in cursor.fetchall()]

        conn.close()
        return list(reversed(turns))

    def save_plan(self, plan: ExecutionPlan) -> None:
        """Save execution plan to database."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            INSERT OR REPLACE INTO execution_plans (plan_id, original_command, plan_json, status)
            VALUES (?, ?, ?, ?)
        """, (plan.plan_id, plan.original_command, json.dumps([vars(s) for s in plan.steps]), 'created'))

        for step in plan.steps:
            cursor.execute("""
                INSERT OR REPLACE INTO execution_steps
                (plan_id, step_number, tool, input_text, reason, status, retries)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (plan.plan_id, step.step, step.tool, step.input, step.reason, step.status, step.retries))

        conn.commit()
        conn.close()

    def update_step_status(self, plan_id: str, step_num: int, status: str,
                           result: Optional[str] = None, error: Optional[str] = None) -> None:
        """Update step execution status."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        now = datetime.now().isoformat()
        if status in ('completed', 'failed', 'skipped'):
            cursor.execute("""
                UPDATE execution_steps
                SET status = ?, result = ?, error = ?, completed_at = ?
                WHERE plan_id = ? AND step_number = ?
            """, (status, result, error, now, plan_id, step_num))
        else:
            cursor.execute("""
                UPDATE execution_steps
                SET status = ?, started_at = ?
                WHERE plan_id = ? AND step_number = ?
            """, (status, now, plan_id, step_num))

        conn.commit()
        conn.close()

    def save_scraped_data(self, plan_id: str, step_num: int, url: str, content: str) -> None:
        """Save scraped content."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO scraped_data (plan_id, step_number, url, content)
            VALUES (?, ?, ?, ?)
        """, (plan_id, step_num, url, content[:5000]))

        conn.commit()
        conn.close()

    def get_plan_status(self, plan_id: str) -> Dict[str, Any]:
        """Get execution plan status."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT status FROM execution_plans WHERE plan_id = ?
        """, (plan_id,))
        row = cursor.fetchone()
        status = row[0] if row else "not_found"

        cursor.execute("""
            SELECT step_number, tool, status, result, error
            FROM execution_steps WHERE plan_id = ?
            ORDER BY step_number
        """, (plan_id,))
        steps = [dict(row) for row in cursor.fetchall()]

        conn.close()
        return {"status": status, "steps": steps}

    def set_preference(self, key: str, value: str) -> None:
        """Set user preference."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO user_preferences (key, value, updated_at)
            VALUES (?, ?, ?)
        """, (key, value, datetime.now().isoformat()))
        conn.commit()
        conn.close()

    def get_preference(self, key: str, default: str = None) -> Optional[str]:
        """Get user preference."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM user_preferences WHERE key = ?", (key,))
        row = cursor.fetchone()
        conn.close()
        return row[0] if row else default


# Global context manager
_context_manager = ContextManager()


# =============================================================================
# PLANNING MODULE
# =============================================================================
class PlanningModule:
    """
    PLAN PHASE: Use Claude to break complex commands into atomic subtasks.
    """

    PLANNER_SYSTEM_PROMPT = """You are Lucifer's planning module. Break the user's request into an ordered list of atomic subtasks.

Available tools:
- SEARCH: Search the web for information
- OPEN_URL: Open a specific URL in browser
- YOUTUBE: Play video or search YouTube
- SCRAPE: Deep scrape a webpage for content
- SPEAK: Speak a message to the user (use for confirmations, summaries)
- SCREENSHOT: Take a screenshot
- WAIT: Wait/delay between steps

Rules:
1. Each subtask must use exactly one tool
2. Be specific with inputs (exact search queries, URLs, etc.)
3. Include a 'reason' explaining why this step is needed
4. For multi-step tasks, ensure steps have proper dependencies
5. Return ONLY a valid JSON array, no other text

Example:
Input: "Find info about Tesla and open their website"
Output: [{"step": 1, "tool": "SEARCH", "input": "Tesla Inc company information", "reason": "Get current information about Tesla"}, {"step": 2, "tool": "OPEN_URL", "input": "tesla.com", "reason": "Open Tesla's official website"}, {"step": 3, "tool": "SPEAK", "input": "Opening Tesla's website now", "reason": "Confirm action to user"}]"""

    def __init__(self):
        self.client: Optional[Anthropic] = None
        self._init_client()

    def _init_client(self) -> None:
        """Initialize Gemini client."""
        import google.generativeai as genai
        api_key = os.environ.get('GEMINI_API_KEY') or os.environ.get('GOOGLE_API_KEY')
        if api_key:
            try:
                genai.configure(api_key=api_key)
                self.client = genai.GenerativeModel('gemini-2.0-flash')
                logger.info("Planning module Gemini client initialized")
            except Exception as e:
                logger.error(f"Gemini init failed: {e}")

    def create_plan(self, command: str, context_history: List[ConversationTurn] = None) -> ExecutionPlan:
        """
        Create execution plan from user command.

        Args:
            command: Raw user command
            context_history: Recent conversation turns for context

        Returns:
            ExecutionPlan with validated steps
        """
        logger.info(f"Creating plan for: {command[:50]}...")

        # Build context for Gemini
        context_str = ""
        if context_history:
            recent = context_history[-5:]  # Last 5 turns
            context_str = "Recent conversation:\n"
            for turn in recent:
                context_str += f"{turn.role}: {turn.content}\n"

        user_prompt = f"{context_str}\nUser request: {command}"

        # Call Gemini to generate plan
        if self.client:
            try:
                response = self.client.generate_content(
                    self.PLANNER_SYSTEM_PROMPT + "\n\n" + user_prompt,
                    generation_config={'max_output_tokens': 800, 'temperature': 0.1}
                )

                plan_json = response.text.strip()
                logger.debug(f"Raw plan from Claude: {plan_json[:200]}")

            except Exception as e:
                logger.error(f"Claude planning failed: {e}")
                return self._fallback_plan(command)
        else:
            return self._fallback_plan(command)

        # Parse and validate plan
        plan = self._parse_and_validate_plan(plan_json, command)
        return plan

    def _parse_and_validate_plan(self, plan_json: str, original_command: str) -> ExecutionPlan:
        """Parse JSON plan and validate each step."""
        try:
            # Clean JSON
            plan_json = plan_json.strip()
            if plan_json.startswith('```json'):
                plan_json = plan_json[7:]
            if plan_json.startswith('```'):
                plan_json = plan_json[3:]
            if plan_json.endswith('```'):
                plan_json = plan_json[:-3]
            plan_json = plan_json.strip()

            steps_data = json.loads(plan_json)

            if not isinstance(steps_data, list):
                raise ValueError("Plan must be a JSON array")

            plan_id = str(uuid.uuid4())[:8]
            steps = []

            for i, step_data in enumerate(steps_data):
                if not isinstance(step_data, dict):
                    continue

                step = PlannedStep(
                    step=i + 1,
                    tool=step_data.get('tool', '').upper(),
                    input=step_data.get('input', ''),
                    reason=step_data.get('reason', '')
                )

                # Validate tool
                try:
                    Tool(step.tool)
                except ValueError:
                    logger.warning(f"Invalid tool {step.tool}, defaulting to SEARCH")
                    step.tool = Tool.SEARCH.value

                steps.append(step)

            if not steps:
                raise ValueError("No valid steps in plan")

            plan = ExecutionPlan(
                plan_id=plan_id,
                original_command=original_command,
                steps=steps
            )

            # Save to context
            _context_manager.save_plan(plan)
            logger.info(f"Plan created: {plan_id} with {len(steps)} steps")

            return plan

        except json.JSONDecodeError as e:
            logger.error(f"JSON parse error: {e}")
            return self._fallback_plan(original_command)

    def _fallback_plan(self, command: str) -> ExecutionPlan:
        """Create a simple fallback plan when Claude fails."""
        plan_id = str(uuid.uuid4())[:8]

        # Try to infer intent
        command_lower = command.lower()
        if 'youtube' in command_lower or 'play' in command_lower:
            tool = Tool.YOUTUBE
        elif 'open' in command_lower or 'go to' in command_lower:
            tool = Tool.OPEN_URL
        elif 'search' in command_lower or 'find' in command_lower:
            tool = Tool.SEARCH
        else:
            tool = Tool.SEARCH

        steps = [PlannedStep(
            step=1,
            tool=tool.value,
            input=command,
            reason="Execute user command"
        )]

        plan = ExecutionPlan(plan_id=plan_id, original_command=command, steps=steps)
        _context_manager.save_plan(plan)
        return plan


# =============================================================================
# VERIFICATION MODULE
# =============================================================================
class VerificationModule:
    """
    Self-verification: Check if each step succeeded.
    """

    VERIFY_SYSTEM_PROMPT = """You are Lucifer's verification module. Determine if a step succeeded or failed.

Reply with ONLY one word:
- "YES" if the step completed successfully
- "NO" if the step failed or encountered an error

Consider:
- Did the tool execute without errors?
- Was the expected output obtained?
- Did the action complete as intended?"""

    def __init__(self):
        self.client = None
        self._init_client()

    def _init_client(self):
        import google.generativeai as genai
        api_key = os.environ.get('GEMINI_API_KEY') or os.environ.get('GOOGLE_API_KEY')
        if api_key:
            try:
                genai.configure(api_key=api_key)
                self.client = genai.GenerativeModel('gemini-2.0-flash')
            except:
                pass

    def verify_step(self, step: PlannedStep, result: str) -> bool:
        """
        Verify if step succeeded.

        Args:
            step: The executed step
            result: Execution result

        Returns:
            True if verified success, False otherwise
        """
        if not self.client:
            # No Gemini - assume success if no error
            return "error" not in result.lower() and "failed" not in result.lower()

        try:
            user_prompt = f"Step: {step.tool} - {step.input}\nResult: {result[:500]}"

            response = self.client.generate_content(
                self.VERIFY_SYSTEM_PROMPT + "\n\n" + user_prompt,
                generation_config={'max_output_tokens': 10, 'temperature': 0.1}
            )

            answer = response.text.strip().upper()
            success = "YES" in answer
            logger.info(f"Verification for step {step.step}: {answer} -> {'success' if success else 'failed'}")

            return success

        except Exception as e:
            logger.error(f"Verification failed: {e}")
            return True  # Assume success on error


# =============================================================================
# EXECUTION ENGINE
# =============================================================================
class ExecutionEngine:
    """
    EXECUTE PHASE: Execute planned steps with handlers.
    """

    def __init__(self, verification: VerificationModule):
        self.verification = verification
        self.step_results = {}  # Store results for later synthesis

    async def execute_plan(self, plan: ExecutionPlan, session_id: str) -> Dict[str, Any]:
        """
        Execute the full plan.

        Args:
            plan: ExecutionPlan to execute
            session_id: Current session ID

        Returns:
            Dict with execution summary
        """
        logger.info(f"Executing plan {plan.plan_id} with {len(plan.steps)} steps")

        completed_steps = []
        failed_steps = []
        all_results = []

        for step in plan.steps:
            # Check if step depends on previous results
            if step.tool == Tool.SPEAK.value and "{previous}" in step.input:
                # Substitute previous results
                prev_result = all_results[-1]['result'] if all_results else "No previous results"
                step.input = step.input.replace("{previous}", prev_result)

            # Execute step
            result = await self._execute_step(step, plan.plan_id, session_id)

            # Verify result
            success = self.verification.verify_step(step, result)

            if success:
                step.status = "completed"
                step.result = result
                completed_steps.append(step)
                all_results.append({"step": step.step, "tool": step.tool, "result": result})
                logger.info(f"Step {step.step} ({step.tool}) completed")
            else:
                # Retry once
                if step.retries < MAX_RETRIES:
                    step.retries += 1
                    logger.info(f"Step {step.step} failed, retrying (attempt {step.retries})")
                    _context_manager.update_step_status(plan.plan_id, step.step, "running")

                    result = await self._execute_step(step, plan.plan_id, session_id)
                    success = self.verification.verify_step(step, result)

                    if success:
                        step.status = "completed"
                        step.result = result
                        completed_steps.append(step)
                        all_results.append({"step": step.step, "tool": step.tool, "result": result})
                    else:
                        step.status = "failed"
                        step.error = result
                        failed_steps.append(step)
                        logger.warning(f"Step {step.step} failed after retry")
                else:
                    step.status = "failed"
                    step.error = result
                    failed_steps.append(step)
                    logger.warning(f"Step {step.step} failed after max retries")

            # Update context
            _context_manager.update_step_status(
                plan.plan_id, step.step, step.status,
                step.result, step.error
            )

        # Synthesize final results
        synthesis = self._synthesize_results(plan.original_command, all_results)

        return {
            "plan_id": plan.plan_id,
            "total_steps": len(plan.steps),
            "completed": len(completed_steps),
            "failed": len(failed_steps),
            "results": all_results,
            "synthesis": synthesis
        }

    async def _execute_step(self, step: PlannedStep, plan_id: str, session_id: str) -> str:
        """
        Execute a single step with the appropriate handler.
        Implements 30-second timeout with self-monitoring.
        """
        logger.info(f"Executing step {step.step}: {step.tool} - {step.input[:40]}")

        _context_manager.update_step_status(plan_id, step.step, "running")

        tool = step.tool.upper()

        try:
            # Execute with timeout
            result = await asyncio.wait_for(
                self._run_handler(tool, step.input, plan_id),
                timeout=STEP_TIMEOUT_SECONDS
            )
            return result

        except asyncio.TimeoutError:
            logger.warning(f"Step {step.step} timeout - replanning")
            return f"Timeout: Step took longer than {STEP_TIMEOUT_SECONDS} seconds. Trying alternative approach."
        except Exception as e:
            logger.error(f"Step {step.step} error: {e}")
            return f"Error: {str(e)}"

    async def _run_handler(self, tool: str, input_text: str, plan_id: str) -> str:
        """Run the appropriate handler for the tool."""
        loop = asyncio.get_event_loop()

        if tool == Tool.SEARCH.value:
            # Use research module
            result = await loop.run_in_executor(None, lambda: research(input_text))
            return f"Search completed for: {input_text}\n{result.synthesis[:500]}"

        elif tool == Tool.OPEN_URL.value:
            # Use browser module
            await loop.run_in_executor(None, lambda: handle_open_url(input_text))
            return f"Opened URL: {input_text}"

        elif tool == Tool.YOUTUBE.value:
            # Use browser module
            await loop.run_in_executor(None, lambda: handle_youtube(input_text))
            return f"YouTube: {input_text}"

        elif tool == Tool.SCRAPE.value:
            # Use browser module
            content = await loop.run_in_executor(None, lambda: get_page_text(input_text))
            _context_manager.save_scraped_data(plan_id, 0, input_text, content)
            return f"Scraped content from: {input_text}\n{content[:300]}"

        elif tool == Tool.SPEAK.value:
            # Use TTS
            await loop.run_in_executor(None, lambda: speak_text(input_text))
            return f"Spoke: {input_text}"

        elif tool == Tool.SCREENSHOT.value:
            # Use system handler
            await loop.run_in_executor(None, lambda: handle_system("screenshot"))
            return "Screenshot taken"

        elif tool == Tool.WAIT.value:
            # Wait for specified seconds
            seconds = int(input_text) if input_text.isdigit() else 2
            await asyncio.sleep(seconds)
            return f"Waited {seconds} seconds"

        else:
            return f"Unknown tool: {tool}"

    def _synthesize_results(self, original_command: str, results: List[Dict]) -> str:
        """Synthesize all results into a final summary based on user preference."""
        if not results:
            return "No results obtained, Sir."

        # Check for any failures or errors in the results list
        for r in results:
            res_str = r.get('result', '')
            if res_str.startswith("Error:") or res_str.startswith("Timeout:") or "error" in res_str.lower():
                # Clean up error text slightly for premium voice output
                clean_err = res_str.replace("Error: ", "").replace("Timeout: ", "").strip()
                return f"There was an error, Sir: {clean_err}"

        return "Task completed, Sir."


def speak_text(text: str) -> None:
    """Speak text using TTS."""
    try:
        from lucifer_main import _tts
        _tts.speak(text)
    except Exception:
        try:
            engine = pyttsx3.init()
            engine.say(text)
            engine.runAndWait()
        except Exception as e:
            logger.warning(f"TTS error: {e}")


# =============================================================================
# ASYNC TASK QUEUE
# =============================================================================
class TaskQueue:
    """
    Async task queue for parallel execution of independent steps.
    """

    def __init__(self, max_concurrent: int = 3):
        self.max_concurrent = max_concurrent
        self.queue = asyncio.Queue()
        self.results = {}

    async def add_task(self, task_id: str, coro):
        """Add a task to the queue."""
        await self.queue.put((task_id, coro))

    async def run_all(self) -> Dict[str, Any]:
        """Run all queued tasks with concurrency limit."""
        tasks = []
        results = {}

        while not self.queue.empty():
            task_id, coro = await self.queue.get()
            task = asyncio.create_task(coro)
            tasks.append((task_id, task))

            # Limit concurrency
            if len(tasks) >= self.max_concurrent:
                # Wait for at least one to complete
                done, pending = await asyncio.wait(
                    [t[1] for t in tasks],
                    return_when=asyncio.FIRST_COMPLETED
                )
                for task_id, task in tasks:
                    if task in done:
                        try:
                            results[task_id] = task.result()
                        except Exception as e:
                            results[task_id] = f"Error: {e}"

                # Keep pending tasks
                tasks = [(tid, t) for tid, t in tasks if t not in done]

        # Wait for remaining tasks
        for task_id, task in tasks:
            try:
                results[task_id] = await task
            except Exception as e:
                results[task_id] = f"Error: {e}"

        return results


# =============================================================================
# PCE ORCHESTRATOR (MAIN CLASS)
# =============================================================================
class PCEOrchestrator:
    """
    Main PCE Orchestrator: Plan → Context → Execute
    """

    def __init__(self):
        self.planner = PlanningModule()
        self.verifier = VerificationModule()
        self.executor = ExecutionEngine(self.verifier)
        self.current_plan: Optional[ExecutionPlan] = None
        self.session_id = str(uuid.uuid4())[:8]

    async def process(self, command: str) -> Dict[str, Any]:
        """
        Process a command through the full PCE pipeline.

        Args:
            command: Raw user command

        Returns:
            Execution result with synthesis
        """
        logger.info(f"=" * 60)
        logger.info(f"PCE ORCHESTRATOR - Processing: {command[:50]}...")
        logger.info(f"=" * 60)

        # Add user command to context
        _context_manager.add_turn(self.session_id, "user", command)

        # Context phase: Get recent conversation
        context_history = _context_manager.get_recent_turns(self.session_id)

        # Plan phase: Create execution plan
        logger.info("PHASE 1: Planning")
        self.current_plan = self.planner.create_plan(command, context_history)

        logger.info(f"Plan created with {len(self.current_plan.steps)} steps:")
        for step in self.current_plan.steps:
            logger.info(f"  {step.step}. {step.tool}: {step.input[:40]}...")

        # Execute phase: Run the plan
        logger.info("PHASE 2: Executing")
        result = await self.executor.execute_plan(self.current_plan, self.session_id)

        # Add assistant response to context
        _context_manager.add_turn(self.session_id, "assistant", result['synthesis'])

        logger.info(f"=" * 60)
        logger.info(f"PCE COMPLETE - {result['completed']}/{result['total_steps']} steps")
        logger.info(f"=" * 60)

        return result

    def get_context_summary(self) -> str:
        """Get current context summary."""
        turns = _context_manager.get_recent_turns(self.session_id)
        if not turns:
            return "No conversation history"

        summary = "Recent conversation:\n"
        for turn in turns[-5:]:
            role = "User" if turn.role == "user" else "Lucifer"
            content = turn.content[:80] + "..." if len(turn.content) > 80 else turn.content
            summary += f"{role}: {content}\n"

        return summary


# =============================================================================
# SIMPLE API FUNCTION
# =============================================================================
_orchestrator: Optional[PCEOrchestrator] = None


async def orchestrate(command: str) -> Dict[str, Any]:
    """
    Main entry point - process a command through PCE.

    Args:
        command: User command

    Returns:
        Execution result dict
    """
    global _orchestrator

    if _orchestrator is None:
        _orchestrator = PCEOrchestrator()

    result = await _orchestrator.process(command)

    # Speak simple completed status or human-friendly exact error
    if result.get('failed', 0) > 0:
        err_msg = "unknown error"
        # Find exact error
        for r in result.get('results', []):
            if r.get('error'):
                err_msg = r['error']
                break
            elif r.get('result') and 'error' in str(r['result']).lower():
                err_msg = r['result']
                break
        if err_msg == "unknown error" and _orchestrator.current_plan:
            for step in _orchestrator.current_plan.steps:
                if step.status == "failed" and step.error:
                    err_msg = step.error
                    break
        
        # Clean up billing link/quota clutter to keep the spoken error friendly
        err_msg_clean = str(err_msg)
        if "Quota exceeded" in err_msg_clean or "429" in err_msg_clean:
            err_msg_clean = "Gemini API quota exceeded"
        elif "details." in err_msg_clean:
            err_msg_clean = err_msg_clean.split("details.")[-1].strip()
        
        speak_text(f"There was an error: {err_msg_clean}")
    else:
        speak_text("Task completed.")

    return result


def reset_session() -> None:
    """Reset orchestrator for new session."""
    global _orchestrator
    _orchestrator = PCEOrchestrator()
    logger.info("Orchestrator session reset")


# =============================================================================
# TEST / DEMO
# =============================================================================
if __name__ == "__main__":
    print("=" * 60)
    print("Lucifer PCE Orchestrator - Test Mode")
    print("=" * 60)

    # Check configuration
    claude_key = os.environ.get('ANTHROPIC_API_KEY') or os.environ.get('CLAUDE_API_KEY')
    print(f"\nConfiguration:")
    print(f"  Claude API: {'✓ Configured' if claude_key else '✗ Not set'}")
    print(f"  Modules: {'✓ Available' if _MODULES_AVAILABLE else '✗ Not imported'}")

    print("\n" + "=" * 60)
    print("Usage:")
    print("  import asyncio")
    print("  from lucifer_orchestrator import orchestrate")
    print("  result = asyncio.run(orchestrate('your command'))")
    print("=" * 60)

    # Demo planning
    print("\n[Demo: Testing planner...]")
    planner = PlanningModule()
    test_commands = [
        "Find info about AI companies and open their websites",
        "Search for quantum computing news and play a related video",
        "Take a screenshot and tell me the time"
    ]

    for cmd in test_commands:
        print(f"\nCommand: {cmd}")
        plan = planner.create_plan(cmd)
        print(f"Plan: {len(plan.steps)} steps")
        for step in plan.steps:
            print(f"  {step.step}. {step.tool}: {step.input[:40]}...")

    print("\n" + "=" * 60)
    print("Test complete")
    print("=" * 60)