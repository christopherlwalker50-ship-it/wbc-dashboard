@echo off
start "Braves Dashboard" /min cmd /k "cd /d C:\Users\CWALK\desktop\claude-playtime\baseball-digest\braves-dashboard && venv\Scripts\activate && streamlit run app.py"
timeout /t 4 /nobreak >nul
start http://localhost:8501
