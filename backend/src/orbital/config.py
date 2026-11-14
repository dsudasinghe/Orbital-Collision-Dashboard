from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")

    database_url: str
    celestrak_user_agent: str = "orbital-collision-dashboard/0.1"
    celestrak_base_url: str = "https://celestrak.org/NORAD/elements/gp.php"
    # CelesTrak updates GP data about every 2 hours and asks clients not to
    # re-download the same data more often than that.
    celestrak_min_interval_s: int = 2 * 60 * 60
    data_dir: Path = BACKEND_DIR / "data"

    spacetrack_identity: str | None = None
    spacetrack_password: SecretStr | None = None

    @property
    def sqlalchemy_url(self) -> str:
        """Supabase hands out postgresql:// URLs; SQLAlchemy needs the psycopg 3 driver named."""
        url = self.database_url
        for prefix in ("postgresql://", "postgres://"):
            if url.startswith(prefix):
                return "postgresql+psycopg://" + url[len(prefix):]
        return url


@lru_cache
def get_settings() -> Settings:
    return Settings()
