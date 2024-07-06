# tg_bot_template
https://t.me/Speakadora_bot
### Project structure

```  
├───grafana_exporter                 # Custom mongo data export
├───nginx-data                       # Nginx config
├───docker-compose.yaml        
├───.env.example                     # Docker compose config file example  
└───src
    ├───assets                       # Place any icons, assets, mediafiles here
    ├───dao                          # Place any external services handlers here
    │   └───__init__.py              # Dao namespace class here. Dont forget do add created module 
    │   └───base.py                  # Base interface. Every DAO class should be derived from it  
    │   └───example.py               # Example dao shows how to use mongodb collection
    │
    ├───managers                     # Low-level external connections handlers here
    │   └───__init__.py              # Managers namespace class here. Dont forget do add created module 
    │   └───database.py              # Mongodb connector
    │   └───session.py               # Any persistent sessions for web requests
    │   └───states.py                # Default telebot groups/states extension
    │
    ├───models                       # Place any model classes here: object data sctuctures, system classes, db models
    │   └───app.py                   # Global App singleton, inherits dict. Use it to access modules and save/share context
    │
    ├───routes                       # Place telegram endpoint handlers here. 
    │   └───example.py               # Example telegram bot endpoint
    │
    add created module 
    │
    ├───utils                        # Place any helpers here
    │   └───
    │
    ├───config.yaml                  # Bot config file. Access it with App()["config"]
    ├───filters.py                   # PyTelegramBotApi based filters
    ├───main.py                      # Start point. System init and routes listing 
    ├───settings.py                  # Config file parsing
    └───swagger.yaml                 # web api docs

```

# С использованием Docker (рекомендуется)
Запуск сборки

``sudo docker compose up -d --build``

Запуск после сборки

``docker compose start``

Выключение

``docker compose stop``

Просмотр логов

``docker logs -f контейнер_id``

# Запуск вне Docker контейнера


```
Клонирование проекта
git clone https://github.com/a-milenkin/LLM_course_project.git
cd LLM_course_project

Установка зависимостей
python3 -m pip install  -U --no-cache-dir -r requirements.txt -c constraints.txt

Запуск проекта
python3 /src/utils/main.py
```


