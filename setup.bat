@echo off
REM setup.bat - one-shot environment setup for arc + tollgate (Windows).
REM
REM Mirror of setup.sh for Windows cmd.exe. Builds a Python venv, installs
REM every workspace package in dependency order with the right extras,
REM and (optionally) the React frontend deps. Two profiles:
REM
REM   --mode dev   (default)   Local development. Adds [dev] extras.
REM   --mode aws               Production-like. Adds [aws] extras.
REM
REM Optional flags:
REM   --with-frontend          Also npm install the React frontends.
REM   --python <path>          Use a specific Python interpreter.
REM   --venv <dir>             Override the venv directory (default: .venv).
REM
REM Idempotent. Stops on first error.
REM
REM Usage:
REM   setup.bat                            # dev profile, no frontend
REM   setup.bat --mode aws                 # AWS profile
REM   setup.bat --mode dev --with-frontend # dev + npm install
REM
REM After it finishes:
REM   .venv\Scripts\activate.bat
REM   arc --help

setlocal EnableExtensions EnableDelayedExpansion

REM cd to the script directory so paths resolve regardless of where it's run from.
cd /d "%~dp0"

REM -- Defaults --------------------------------------------------------------
set "MODE=dev"
set "WITH_FRONTEND=0"
set "PYTHON_BIN=python"
if not "%PYTHON%"=="" set "PYTHON_BIN=%PYTHON%"
set "VENV_DIR=.venv"
set "MIN_PY_MAJOR=3"
set "MIN_PY_MINOR=11"

REM -- Parse args ------------------------------------------------------------
:parse_args
if "%~1"=="" goto args_done
if /i "%~1"=="--mode" (
    set "MODE=%~2"
    shift & shift & goto parse_args
)
if /i "%~1"=="--with-frontend" (
    set "WITH_FRONTEND=1"
    shift & goto parse_args
)
if /i "%~1"=="--python" (
    set "PYTHON_BIN=%~2"
    shift & shift & goto parse_args
)
if /i "%~1"=="--venv" (
    set "VENV_DIR=%~2"
    shift & shift & goto parse_args
)
if /i "%~1"=="-h" goto show_help
if /i "%~1"=="--help" goto show_help
echo Unknown argument: %~1>&2
echo Run with --help for usage.>&2
exit /b 1
:args_done

if /i not "%MODE%"=="dev" if /i not "%MODE%"=="aws" (
    echo X --mode must be 'dev' or 'aws' (got '%MODE%')>&2
    exit /b 1
)

REM -- 1. Python version check ----------------------------------------------
echo.
echo == Checking Python interpreter...
where %PYTHON_BIN% >nul 2>&1
if errorlevel 1 (
    echo X %PYTHON_BIN% not found. Install Python %MIN_PY_MAJOR%.%MIN_PY_MINOR%+ or pass --python.>&2
    exit /b 1
)
for /f "tokens=*" %%V in ('%PYTHON_BIN% -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')"') do set "PY_VERSION=%%V"
for /f "tokens=*" %%V in ('%PYTHON_BIN% -c "import sys; print(sys.version_info.major)"') do set "PY_MAJOR=%%V"
for /f "tokens=*" %%V in ('%PYTHON_BIN% -c "import sys; print(sys.version_info.minor)"') do set "PY_MINOR=%%V"
if %PY_MAJOR% LSS %MIN_PY_MAJOR% goto py_too_old
if %PY_MAJOR% EQU %MIN_PY_MAJOR% if %PY_MINOR% LSS %MIN_PY_MINOR% goto py_too_old
echo OK Python %PY_VERSION%
goto py_ok
:py_too_old
echo X Python %MIN_PY_MAJOR%.%MIN_PY_MINOR%+ required (found %PY_VERSION%).>&2
exit /b 1
:py_ok

REM -- 2. Create or reuse venv ---------------------------------------------
if exist "%VENV_DIR%\Scripts\python.exe" (
    echo == Reusing existing venv at %VENV_DIR%
) else (
    echo == Creating venv at %VENV_DIR%
    %PYTHON_BIN% -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo X venv creation failed.>&2
        exit /b 1
    )
)

REM Resolve absolute path for venv (handles both relative and absolute --venv args).
for %%I in ("%VENV_DIR%") do set "VENV_ABS=%%~fI"
set "VENV_PY=%VENV_ABS%\Scripts\python.exe"
if not exist "%VENV_PY%" (
    echo X venv Python not at %VENV_PY% -- venv creation failed.>&2
    exit /b 1
)

echo == Upgrading pip
"%VENV_PY%" -m pip install --quiet --upgrade pip
if errorlevel 1 exit /b 1

REM -- 3-4. Install packages in dependency order ----------------------------
echo.
echo == Installing Python packages in '%MODE%' mode (editable, dependency order)...

REM Order matters: tollgate, arc-core, then leaves. Each line:
REM   call :install_pkg "<path>" "<dev-extras>" "<aws-extras>"
call :install_pkg "tollgate"                          ""                                  "aws"
if errorlevel 1 exit /b 1
call :install_pkg "arc\packages\arc-core"             "dev"                               "aws"
if errorlevel 1 exit /b 1
call :install_pkg "arc\packages\arc-connectors"       ""                                  "aws,litellm,outlook,pega,servicenow"
if errorlevel 1 exit /b 1
call :install_pkg "arc\packages\arc-harness"          "dev"                               ""
if errorlevel 1 exit /b 1
call :install_pkg "arc\packages\arc-eval"             "dev"                               ""
if errorlevel 1 exit /b 1
call :install_pkg "arc\packages\arc-orchestrators"    "langchain,langgraph"               "all"
if errorlevel 1 exit /b 1
call :install_pkg "arc\packages\arc-runtime"          "dev"                               "aws"
if errorlevel 1 exit /b 1
call :install_pkg "arc\packages\arc-cli"              "dev"                               ""
if errorlevel 1 exit /b 1
call :install_pkg "arc\packages\arc-platform"         "dev"                               ""
if errorlevel 1 exit /b 1
call :install_pkg "agent-team-template"               "dev"                               ""
if errorlevel 1 exit /b 1
echo OK All Python packages installed.

REM -- 5. Optional frontend -------------------------------------------------
if "%WITH_FRONTEND%"=="1" (
    set "FRONTEND_DIR=arc\packages\arc-platform\frontend"
    if not exist "!FRONTEND_DIR!" (
        echo ! frontend dir !FRONTEND_DIR! missing -- skipping
    ) else (
        where npm >nul 2>&1
        if errorlevel 1 (
            echo ! npm not on PATH -- skipping frontend install
        ) else (
            echo == Installing frontend npm workspaces ^(ops + dev + shared^)...
            pushd "!FRONTEND_DIR!"
            call npm install --silent
            set "NPM_ERR=!errorlevel!"
            popd
            if not "!NPM_ERR!"=="0" (
                echo X npm install failed.>&2
                exit /b 1
            )
            echo OK Frontend deps installed.
        )
    )
)

REM -- 6. Bootstrap .env on first run ---------------------------------------
REM Copy .env.example to .env so subsequent runs find a starter config.
REM Never overwrites an existing .env. Production deploys never run this
REM script, so there's no risk of shipping defaults.
if exist ".env.example" (
    if not exist ".env" (
        copy ".env.example" ".env" >nul
        echo OK Created .env from .env.example -- fill in real values before running real connectors.
    ) else (
        echo == Reusing existing .env ^(won't overwrite^).
    )
)

REM -- 7. Smoke check -------------------------------------------------------
echo.
echo == Smoke-checking the install...
"%VENV_PY%" -c "import arc.core; import tollgate" >nul 2>&1
if errorlevel 1 (
    echo X Sanity import failed -- arc.core / tollgate not importable from the venv.>&2
    exit /b 1
)
set "ARC_BIN=%VENV_ABS%\Scripts\arc.exe"
if exist "%ARC_BIN%" (
    "%ARC_BIN%" --help >nul 2>&1
    if errorlevel 1 (
        echo ! 'arc' CLI is installed but '--help' failed.
    ) else (
        echo OK 'arc' CLI works.
    )
) else (
    echo ! 'arc' CLI not found at %ARC_BIN% -- arc-cli may have failed to install.
)

REM -- 8. Done --------------------------------------------------------------
echo.
echo OK Setup complete (mode=%MODE%).
echo.
echo Next steps:
echo   %VENV_DIR%\Scripts\activate.bat
echo   arc --help
echo   pytest arc\packages
echo.
echo Try the demo:
echo   docs\guides\demo.md
echo.
exit /b 0

REM ── Subroutine: install one package ──────────────────────────────────────
REM Usage: call :install_pkg "<path>" "<dev-extras>" "<aws-extras>"
:install_pkg
set "PKG=%~1"
set "EXTRAS_DEV_ARG=%~2"
set "EXTRAS_AWS_ARG=%~3"
if not exist "%PKG%" (
    echo ! skipping %PKG% -- directory not present
    exit /b 0
)
if /i "%MODE%"=="dev" (
    set "EXTRAS=%EXTRAS_DEV_ARG%"
) else (
    set "EXTRAS=%EXTRAS_AWS_ARG%"
)
if "!EXTRAS!"=="" (
    set "TARGET=%PKG%"
    echo     %PKG% extras=--
) else (
    set "TARGET=%PKG%[!EXTRAS!]"
    echo     %PKG% extras=!EXTRAS!
)
"%VENV_PY%" -m pip install --quiet --editable "!TARGET!"
if errorlevel 1 (
    echo X pip install failed for !TARGET!>&2
    exit /b 1
)
exit /b 0

REM ── Help text ────────────────────────────────────────────────────────────
:show_help
echo.
echo setup.bat -- one-shot environment setup for arc + tollgate
echo.
echo Usage:
echo   setup.bat [--mode dev^|aws] [--with-frontend] [--python ^<path^>] [--venv ^<dir^>]
echo.
echo Modes:
echo   --mode dev   (default)   Local development. Adds [dev] extras.
echo   --mode aws               Production-like. Adds [aws] extras.
echo.
echo Optional:
echo   --with-frontend          Also npm install the React frontends.
echo   --python ^<path^>          Use a specific Python interpreter.
echo   --venv ^<dir^>             Override venv dir (default: .venv).
echo.
echo Examples:
echo   setup.bat
echo   setup.bat --mode aws
echo   setup.bat --mode dev --with-frontend
echo.
exit /b 0
