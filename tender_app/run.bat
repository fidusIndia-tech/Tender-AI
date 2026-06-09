@echo off
REM *** PASTE YOUR OPENAI API KEY BELOW (replace everything after the = sign) ***
set OPENAI_API_KEY=your-openai-api-key-here

echo Starting AI Tender Management System...
echo Open http://localhost:8000 in your browser
echo Press Ctrl+C to stop

C:\Users\dell\AppData\Local\Programs\Python\Python311\python.exe -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
