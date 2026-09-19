import os
import multiprocessing
import logging
import logging.config
import uvicorn

try:
    import uvloop
    loop_type = "uvloop"
except ImportError:
    loop_type = "asyncio"

if __name__ == "__main__":
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ytdl.settings")
    workers = min(max(multiprocessing.cpu_count(), 2), 8)

    log_config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "format": "[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S",
            },
        },
        "handlers": {
            "file": {
                "class": "logging.FileHandler",
                "filename": "server.log",
                "formatter": "default",
            },
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "default",
            },
        },
        "root": {
            "handlers": ["file", "console"],
            "level": "INFO",
        },
    }

    logging.config.dictConfig(log_config)

    uvicorn.run(
        "ytdl.asgi:application",
        host="0.0.0.0",
        port=8005,
        workers=workers,
        reload=False,
        log_level="info",
        log_config=log_config,
        loop=loop_type,
        http="httptools",
        proxy_headers=True,
        forwarded_allow_ips="*",
    )
