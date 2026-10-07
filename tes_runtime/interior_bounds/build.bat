@echo off
REM Build InteriorBounds.dll into build\ (the full plugin) and build\look\
REM (the look-only variant, compiled without the write path).
REM
REM It shares no code with the other runtimes and needs neither the Address
REM Library nor any engine contract, so it compiles alone. /MT: the only DLL
REM it imports from is KERNEL32.

setlocal
call "%~dp0..\common\msvc.bat"
if errorlevel 1 exit /b 1

cd /d "%~dp0"
if not exist obj mkdir obj
if not exist obj\look mkdir obj\look
if not exist build\look mkdir build\look

set FLAGS=/nologo /LD /EHsc /std:c++17 /O2 /MT /W3 /DNDEBUG
set SOURCES=interior_bounds.cpp locate.cpp detour.cpp

echo [build] compiling InteriorBounds...
cl %FLAGS% %SOURCES% apply.cpp /Fo:obj\ /Fe:build\InteriorBounds.dll ^
   /link /IMPLIB:obj\InteriorBounds.lib /OPT:REF /OPT:ICF
if errorlevel 1 goto failed

echo [build] compiling InteriorBounds, look-only...
cl %FLAGS% /DINTERIOR_BOUNDS_LOOK_ONLY %SOURCES% /Fo:obj\look\ ^
   /Fe:build\look\InteriorBounds.dll ^
   /link /IMPLIB:obj\look\InteriorBounds.lib /OPT:REF /OPT:ICF
if errorlevel 1 goto failed

echo [build] OK -^> %~dp0build\InteriorBounds.dll
endlocal
exit /b 0

:failed
echo [build] ERROR: InteriorBounds failed
exit /b 1
