@echo off
rem Open the workbench on CPython 3.11 rather than whatever `python` resolves
rem to.  It matters for the SLOT container: 3.14 bundles zlib-ng, which packs
rem a few per cent looser than the real zlib, and that is the difference
rem between a block fitting its footprint and being left in English.  The
rem app's own subprocesses use sys.executable, so they follow this one.
rem
rem Zopfli is used where installed, for the few blocks zlib cannot pack.
rem Uncomment to turn it off and get the plain-zlib behaviour instead.
rem set PWTR_ZOPFLI=0

setlocal
set PY311=%LOCALAPPDATA%\Programs\Python\Python311\python.exe
if not exist "%PY311%" (
    echo CPython 3.11 is not where this script expects it:
    echo     %PY311%
    echo Edit pwtr311.bat, or run: py -3.11 pwtr.py
    exit /b 1
)
"%PY311%" "%~dp0pwtr.py" %*
