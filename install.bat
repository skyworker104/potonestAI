@echo off
rem PhotoNest AI 설치 스크립트 — Windows
cd /d "%~dp0"
chcp 65001 >nul

echo 📷 PhotoNest AI 설치를 시작합니다
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo ❌ Python이 없습니다. https://www.python.org/downloads/ 에서 설치할 때
  echo    "Add Python to PATH"를 꼭 체크하고 다시 실행하세요.
  pause & exit /b 1
)
python -c "import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)"
if errorlevel 1 (
  echo ❌ Python 3.9 이상이 필요합니다.
  pause & exit /b 1
)
echo ✅ Python 확인 완료

set AI=1
set /p ans="AI 자연어 검색을 설치할까요? 약 2GB 다운로드 [Y/n] "
if /i "%ans%"=="n" set AI=0

if not exist .venv python -m venv .venv
.venv\Scripts\pip install --upgrade pip -q
echo 📦 기본 패키지 설치 중…
.venv\Scripts\pip install -q -r backend\requirements-base.txt
if "%AI%"=="1" (
  echo 🧠 AI 패키지 설치 중… ^(수 분 소요^)
  .venv\Scripts\pip install -q -r backend\requirements-ai.txt
)

(
echo @echo off
echo cd /d "%%~dp0"
echo set AI_SEARCH=%AI%
echo echo 📷 PhotoNest AI — 브라우저에서 http://localhost:8765 를 여세요
echo .venv\Scripts\uvicorn backend.main:app --host 0.0.0.0 --port 8765
) > run.bat

if not exist photos mkdir photos
echo.
echo ✅ 설치 완료!
echo    1^) 사진/동영상을 photos 폴더에 넣으세요
echo    2^) run.bat 으로 실행하세요
echo    3^) 브라우저에서 http://localhost:8765 접속
pause
