@echo off
:restart
echo [%date% %time%] Запуск бота...
python main.py
echo [%date% %time%] Бот завершился. Перезапуск через 5 секунд...
timeout /t 5 /nobreak >nul
goto restart
