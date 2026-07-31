"""PSJBA — zipapp 진입점"""
import sys
import os

# 현재 디렉토리를 PYTHONPATH에 추가 (zipapp 내부 구조용)
sys.path.insert(0, os.path.dirname(__file__))

from main import main

if __name__ == "__main__":
    sys.exit(main())