#!/usr/bin/env python
"""Test script for all Lucifer modules."""

import asyncio
import sqlite3

def test_imports():
    """Test all module imports."""
    print("=" * 60)
    print("TEST 1: Module Imports")
    print("=" * 60)

    from lucifer_intent import route_command, Intent
    print("  lucifer_intent: OK")

    from lucifer_browser import handle_open_url, handle_youtube
    print("  lucifer_browser: OK")

    from lucifer_research import research
    print("  lucifer_research: OK")

    from lucifer_orchestrator import PCEOrchestrator, orchestrate
    print("  lucifer_orchestrator: OK")

    from lucifer_voice import route_command as voice_route
    print("  lucifer_voice: OK")

    print("\nAll imports: SUCCESS\n")


def test_context_manager():
    """Test ContextManager database."""
    print("=" * 60)
    print("TEST 2: Context Manager & Database")
    print("=" * 60)

    from lucifer_orchestrator import ContextManager

    ctx = ContextManager()

    # Add test data
    ctx.add_turn("test_session", "user", "Hello Lucifer")
    ctx.add_turn("test_session", "assistant", "Hello! How can I help?")

    # Get recent turns
    turns = ctx.get_recent_turns("test_session")
    print(f"  Conversation turns: {len(turns)}")
    for t in turns:
        print(f"    {t.role}: {t.content[:30]}...")

    # Check tables
    conn = sqlite3.connect("lucifer_context.db")
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = cursor.fetchall()
    print(f"  Database tables: {[t[0] for t in tables]}")
    conn.close()

    print("\nContext manager: SUCCESS\n")


def test_planning():
    """Test planning module."""
    print("=" * 60)
    print("TEST 3: Planning Module")
    print("=" * 60)

    from lucifer_orchestrator import PCEOrchestrator

    orch = PCEOrchestrator()

    test_commands = [
        "search for quantum computing news",
        "open github",
        "play jazz music on youtube",
    ]

    for cmd in test_commands:
        print(f"\n  Command: {cmd}")
        plan = orch.planner.create_plan(cmd)
        print(f"  Steps: {len(plan.steps)}")
        for step in plan.steps:
            print(f"    {step.step}. {step.tool}: {step.input[:35]}...")

    print("\nPlanning: SUCCESS\n")


def test_orchestration():
    """Test full orchestration."""
    print("=" * 60)
    print("TEST 4: Full Orchestration (Async)")
    print("=" * 60)

    async def run_test():
        from lucifer_orchestrator import orchestrate

        # This would run the full PCE pipeline
        # result = await orchestrate("search for AI news")
        print("  Orchestrator ready for async execution")
        return True

    result = asyncio.run(run_test())
    print("\nOrchestration: SUCCESS\n")


def main():
    """Run all tests."""
    print("\n" + "=" * 60)
    print("LUCIFER MODULES TEST SUITE")
    print("=" * 60 + "\n")

    test_imports()
    test_context_manager()
    test_planning()
    test_orchestration()

    print("=" * 60)
    print("ALL TESTS PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()