# Browser Agent — 기획 명세서 v0.1

> **작성일**: 2026-07-20
> **프로젝트명**: Browser Agent (가칭)
> **기반 기술**: Python 3.11+, LangGraph, Playwright (Python), asyncio, PySide6

---

## 1. 개요

Windows 환경에서 Chrome 브라우저의 CDP(Chrome DevTools Protocol)를 통해 브라우저를 자동 제어하는 에이전트 프로그램. 사용자가 자연어 프롬프트를 입력하면 LangGraph 기반 에이전트가 PlaywrightManager를 통해 브라우저를 조작한다.

---

## 2. 시스템 아키텍처

전체 구조는 4개 레이어로 구성된다:

- **PySide6 UI**: 사용자 인터페이스. 메인 윈도우는 프롬프트 입력창 + Chrome 연결 버튼(우측 상단) + 설정 버튼(좌측 상단) + 세션 로그 영역으로 구성. 설정은 별도 QDialog(설정/CRON/로그 탭). 언어 설정에 따라 에러 메시지 출력 (기본: 한국어)

  **세션 로그 영역** — 메인 윈도우 상단에 위치, 스크롤 가능한 QScrollArea로 구현:
  - **사용자 메시지**: 우측 정렬. 요청 텍스트 + 사람 아이콘(👤 또는 SVG) 표시
  - **AI 응답**: 좌측 정렬. 로봇 아이콘(🤖 또는 SVG) + 응답 내용 표시
  - **툴 콜 (Tool Call)**: AI 응답 내에서 각 도구 호출은 **접힌 상태(collapsible)**로 기본 표시. 사용자가 클릭하여 펼칠 수 있음
  - **최종 답변**: AI 응답의 마지막 메시지(도구 호출 이후의 최종 텍스트 응답)는 **접지 않고** 그대로 보여줌
  - 즉, AI 응답 블록 하나 = (툴 콜들 → 접힘) + (최종 답변 → 펼침) 구조
- **LangGraph Agent**: LLM + 도구 호출 오케스트레이션. 상태 그래프 기반 에이전틱 루프
- **PlaywrightManager**: Python Playwright 라이브러리를 직접 래핑한 서비스 클래스. LangGraph 노드가 직접 import하여 브라우저 제어
- **Chrome (CDP)**: 실제 브라우저. --remote-debugging-port로 열려 있음

### 핵심 원칙
- 모든 계층 asyncio 기반: UI(qasync) → LangGraph(async) → PlaywrightManager(async)
- PlaywrightManager는 서비스 클래스로, LangGraph 노드에서 직접 import하여 Playwright API를 호출
- 프로그램 종료 시 Chrome은 유지. Chrome 프로세스를 종료하지 않음. 재실행 시 기존 CDP 세션 재사용
- **파일 접근 샌드박스**: 프로그램이 읽고 쓸 수 있는 경로는 설정 파일, DB 파일, 워크스페이스 폴더로 제한