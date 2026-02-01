import logging

def get_logger(name: str = "Cost_opt" , level: str = "INFO"):
    logger = logging.getLogger(name)
    logger.propagate = False

    level_value = getattr(logging , level.upper(), logging.INFO)

    logger.setLevel(level_value)
    if not logger.handlers:
        
        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )

        handler = logging.StreamHandler()
        handler.setLevel(level_value)
        handler.setFormatter(formatter)

        logger.addHandler(handler)

    return logger