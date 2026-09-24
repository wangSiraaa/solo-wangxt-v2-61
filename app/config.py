import os


class Settings:
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./pilot_scheduling.db")


settings = Settings()
