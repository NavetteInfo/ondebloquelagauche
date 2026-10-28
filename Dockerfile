FROM python:3.12-slim

WORKDIR /app
COPY refresh.py server.py visits.py index.html favicon.svg robots.txt sitemap.xml ./
COPY css ./css
COPY js ./js
COPY data/fiches.json ./data/fiches.json
COPY fonts ./fonts
COPY media ./media

ENV HOST=0.0.0.0
ENV PORT=8080
ENV REFRESH_SECONDS=60
EXPOSE 8080

CMD ["python3", "server.py"]
