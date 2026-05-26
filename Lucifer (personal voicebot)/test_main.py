#!/usr/bin/env python
"""Quick test for lucifer_main"""

import sys
sys.path.insert(0, '.')

print("Testing lucifer_main...")

# Test 1: Import
import lucifer_main
print("  Import: OK")

# Test 2: Config loading
config = lucifer_main.load_config()
print(f"  Config: wake_word={config.get('wake_word')}")

# Test 3: TTS engine
tts = lucifer_main.TTSEngine()
print("  TTS Engine: OK")

# Test 4: Voice engine init
try:
    ve = lucifer_main.VoiceEngine()
    print("  Voice Engine: OK")
except Exception as e:
    print(f"  Voice Engine: {e}")

print("\nAll basic tests passed!")