from pydantic_settings import BaseSettings
from typing import List
import os

class Settings(BaseSettings):
    DATABASE_URL: str = "sqlite:///./jaasiel_rms.db"
    SECRET_KEY: str = "change-this-secret-key-in-production-must-be-32-chars-min"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30
    APP_ENV: str = "development"
    DEBUG: bool = True
    ALLOWED_ORIGINS: str = "http://localhost:8000,http://localhost:3000"
    API_V1_PREFIX: str = "/api/v1"
    UPLOAD_DIR: str = "./uploads"
    MAX_FILE_SIZE_MB: int = 20
    OPENAI_API_KEY: str = ""   # Legacy — no longer used
    ANTHROPIC_API_KEY: str = ""   # Claude AI — used for OCR score extraction
    SCHOOL_NAME: str = "Jaasiel Education Centre"
    SCHOOL_ADDRESS: str = "Oxygen Street, Benin City, Edo State"
    SCHOOL_PHONE: str = "+234 703 630 4408"
    SCHOOL_EMAIL: str = "admin@jaasiel.edu.ng"
    SCHOOL_MOTTO: str = "Accurate Knowledge is a Virtue"
    PRINCIPAL_NAME: str = "The Principal"

    @property
    def allowed_origins_list(self) -> List[str]:
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",")]

    model_config = {"env_file": ".env", "extra": "ignore"}

settings = Settings()