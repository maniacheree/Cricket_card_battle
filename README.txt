CRICKET CARD ARENA — TELEGRAM WEB APP

FILES
- index.html: Telegram Mini App frontend.
- server.py: backend skeleton that verifies Telegram initData.
- requirements.txt
- .env.example

SETUP
1. Deploy server.py on HTTPS hosting.
2. Set BOT_TOKEN as a server environment variable.
3. Configure BotFather Menu Button / Main Mini App with the HTTPS URL of index.html.
4. If frontend and backend are on different domains, change the API URLs in index.html from relative "/api/..." to your backend HTTPS URL.
5. Add a real database and private screenshot storage before accepting real deposits.
6. Admin approval must credit coins server-side only.

SECURITY
Never put BOT_TOKEN in index.html or GitHub.
Never trust initDataUnsafe for authorization; server.py verifies initData.
Do not store payment screenshots in localStorage.
