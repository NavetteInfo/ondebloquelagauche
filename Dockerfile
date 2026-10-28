FROM python:3.12-slim

WORKDIR /app
COPY refresh.py server.py visits.py pulls.py index.html favicon.svg robots.txt sitemap.xml ./
COPY css ./css
COPY js ./js
COPY data/fiches.json ./data/fiches.json
COPY data/submissions.json ./data/submissions.json
COPY data/comparaison.json ./data/comparaison.json
COPY data/abonnes.json ./data/abonnes.json
COPY data/historique ./data/historique
COPY fonts ./fonts
COPY media ./media

ENV HOST=0.0.0.0
ENV PORT=8080
ENV REFRESH_SECONDS=300
EXPOSE 8080

RUN useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin site \
    && mkdir -p /app/data/raw \
        /app/data/historique/accounts \
        /app/data/historique/counts \
        /app/data/historique/submissions \
        /app/data/historique/fiches \
    && chown -R site:site /app/data
USER site

CMD ["python3", "server.py"]
