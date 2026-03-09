import logging


def get_logger(name: str, level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    h = logging.StreamHandler()
    fmt = logging.Formatter("[%(asctime)s] %(levelname)s %(name)s - %(message)s")
    h.setFormatter(fmt)
    logger.addHandler(h)
    logger.propagate = False
    return logger
