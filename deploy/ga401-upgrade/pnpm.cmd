@echo off
setlocal
"C:\Program Files\nodejs\node.exe" "C:\Users\Marck\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules\pnpm\bin\pnpm.mjs" %*
exit /b %ERRORLEVEL%
