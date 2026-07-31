# PSJBA Project

## 폴더 구조

```
psjba/
├── docs/                # 문서
│   ├── plan/            # 기획 문서 (plan_01~05)
│   │   ├── plan_01_core.md
│   │   ├── plan_02_config_ui.md
│   │   ├── plan_03_agent.md
│   │   ├── plan_04_ops.md
│   │   └── plan_05_review.md
│   └── spec/            # 상세 명세 (추후)
├── src/                 # 소스 코드
├── tests/               # 테스트 코드
├── scripts/             # 빌드/배포/유틸리티 스크립트
├── config/              # 설정 파일
└── README.md
```

## 표준 폴더 구조 설명

| 폴더 | 용도 |
|------|------|
| `docs/plan/` | 기획, 요구사항, 기획서 — **plan[1-5].md 같은 기획 파일은 여기** |
| `docs/spec/` | 상세 설계, API 명세, 아키텍처 문서 |
| `src/` | 실제 구현 코드 (agent가 코딩할 대상) |
| `tests/` | 단위/통합/E2E 테스트 |
| `scripts/` | 빌드, 배포, DB 마이그레이션 등 자동화 스크립트 |
| `config/` | 환경별 설정 파일 (dev/staging/prod) |
