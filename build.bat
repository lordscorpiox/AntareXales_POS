@echo off
setlocal
cd /d "%~dp0"
title AntareXales POS - Compilacion

echo ==========================================
echo   AntareXales POS - generando dist\AntareXales_POS.exe
echo ==========================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] No se encontro Python en el PATH. Instale Python 3.13 o superior desde python.org
    echo         y marque "Add python.exe to PATH" durante la instalacion.
    goto :fin_error
)

echo [1/3] Instalando dependencias...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 goto :fin_error

echo.
echo [2/3] Compilando con PyInstaller...
python -m PyInstaller --noconfirm --clean --onefile --windowed ^
    --name AntareXales_POS ^
    --icon "assets\icon.ico" ^
    --add-data "frontend;frontend" ^
    --add-data "assets;assets" ^
    --hidden-import stock --hidden-import errors --hidden-import inventario ^
    --hidden-import caja --hidden-import compras --hidden-import respaldo --hidden-import actualizador --hidden-import fiscal --hidden-import libros ^
    --collect-all webview ^
    main.py
if errorlevel 1 goto :fin_error

echo.
echo [3/3] Listo.
echo.
echo   Ejecutable: %~dp0dist\AntareXales_POS.exe
echo   La base de datos se guarda en: %%APPDATA%%\AntareXalesPOS\antarexales.db
echo.
pause
exit /b 0

:fin_error
echo.
echo [ERROR] La compilacion fallo. Revise los mensajes anteriores.
pause
exit /b 1
