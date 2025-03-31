import logging

def setup_logger(name: str, log_file: str, level: int = logging.INFO) -> logging.Logger:
    """
    Sets up a custom logger.

    Args:
        name (str): Name of the logger.
        log_file (str): File path for the log file.
        level (int): Logging level (default: logging.INFO).

    Returns:
        logging.Logger: Configured logger instance.
    """
    logger = logging.getLogger(name)

    # Disable propagation to prevent interference from the root logger
    logger.propagate = False

    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    file_handler = logging.FileHandler(log_file)
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    logger.setLevel(level)
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger

# Example usage
log = setup_logger("bot", "bot.log")
log.info("Logger initialized.")