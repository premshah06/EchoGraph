"""Configuration module for environment variables and settings."""

import os
from typing import Optional
from pydantic_settings import BaseSettings
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""
    
    # OpenAI Configuration
    openai_api_key: Optional[str] = None
    
    # Server Configuration
    host: str = "0.0.0.0"
    port: int = 8000
    
    # ChromaDB Configuration
    chromadb_persist_dir: str = "./echosystem_db"
    
    # CORS Configuration
    allowed_origins: str = "http://localhost:3000,http://localhost:8000"
    
    # Demo Mode
    demo_mode: bool = False

    # API Key Authentication (comma-separated; auth disabled if empty)
    api_keys: str = ""

    # Runtime tuning
    log_level: str = "INFO"
    workers: int = 2

    class Config:
        env_file = ".env"
        case_sensitive = False

    @property
    def is_openai_configured(self) -> bool:
        """Check if OpenAI API key is configured."""
        return self.openai_api_key is not None and len(self.openai_api_key) > 0

    @property
    def allowed_origins_list(self) -> list:
        """Get CORS allowed origins as a list."""
        return [origin.strip() for origin in self.allowed_origins.split(',')]

    @property
    def api_keys_set(self) -> set:
        """Get configured API keys as a set. Empty set means auth is disabled."""
        return {key.strip() for key in self.api_keys.split(",") if key.strip()}


# Global settings instance
settings = Settings()


def get_settings() -> Settings:
    """Get the global settings instance."""
    return settings
