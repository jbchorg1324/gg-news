# 경기남부 뉴스지도 2.0

GitHub Pages와 GitHub Actions 기반 정적 뉴스 대시보드입니다.

## 시작하기
1. 이 압축파일 **내용물 전체**를 GitHub 저장소 `main` 브랜치 최상위에 업로드하세요. `.github/workflows/update.yml`의 숨김 폴더 구조를 유지해야 합니다.
2. `Settings → Actions → General → Workflow permissions`에서 **Read and write permissions** 선택 후 저장하세요. (조직 정책에 따라 설정 불가할 수 있음)
3. `Actions → Update Gyeonggi South news → Run workflow`를 한 번 실행하세요.
4. `Settings → Pages → Build and deployment`에서 **Deploy from a branch**, `main`, `/(root)`를 고르고 저장하세요.
5. 수 분 뒤 `https://jbchorg1324.github.io/gg-news/`에서 접속하세요.

## 주기 및 데이터의 한계
- 매시간 7분경 GitHub Actions 실행 예약. GitHub 사정에 따라 지연되거나 실행이 누락될 수 있습니다.
- 뉴스 데이터는 Google News 공개 RSS로 수집; 게재·발행 시각 기준이며 직접 취재한 팩트체크 결과가 아닙니다.
- '속보'와 '사건·사고'는 **기사 제목**의 키워드로 분류합니다. 실제 발생 시각 기준 12시간 사건 집계가 아닙니다.
- '관심 뉴스'는 실제 조회수 인기순이 아닌 최신 기사 목록입니다.
- 날씨는 **Open-Meteo**, 기상청이 아닙니다. 오전 8시대 실행 때 갱신을 시도합니다. 최초 수집은 언제 실행하든 날씨 정보를 채웁니다.
- 지도는 개념도이며 실제 행정구역 경계가 아닙니다.
- RSS 수집이 실패하면 기존 기사를 남기고 상태에 오류를 표시합니다. 새로 수집한 것처럼 시각을 변경하지 않습니다.
- 주소는 기존 chatgpt.site URL이 아니라 **GitHub Pages의 별도 URL**입니다.
