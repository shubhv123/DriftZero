from pydantic import BaseSettings, Field

class AppSettings(BaseSettings):
    app_name: str = Field("LegacyPaymentService", env="APP_NAME")
    opneai_api_key: str = Field(..., env="OPENAI_API_KEY")
    max_retries: int = Field(3, ge=1, le=10)
    timeout_seconds: float = 15.0

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False
    
def get_settings_dict() -> dict:
    settings = AppSettings(_env_file=None, opneai_api_key="sk-test-12345")
    return settings.dict()