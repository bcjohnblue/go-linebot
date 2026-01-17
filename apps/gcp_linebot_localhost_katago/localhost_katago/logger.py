import logging
import colorlog

# Create logger
logger = logging.getLogger("go_linebot")
logger.setLevel(logging.DEBUG)

# Create console handler with colorlog
handler = colorlog.StreamHandler()
handler.setLevel(logging.DEBUG)

# Create formatter with colors
formatter = colorlog.ColoredFormatter(
    "%(log_color)s%(levelname)s%(reset)s: %(message)s",
    log_colors={
        "DEBUG": "cyan",
        "INFO": "green",
        "WARNING": "yellow",
        "ERROR": "red",
        "CRITICAL": "red,bg_white",
    },
    secondary_log_colors={},
    style="%",
)

handler.setFormatter(formatter)
logger.addHandler(handler)

# Prevent duplicate handlers
if len(logger.handlers) > 1:
    logger.handlers = [handler]

