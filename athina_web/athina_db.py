import os


class db_info:
    athina_mysql_host = None
    athina_mysql_port = None
    athina_mysql_username = None
    athina_mysql_password = None

    def __init__(self):
        self.athina_mysql_host = os.environ.get("ATHINA_MYSQL_HOST", None)
        self.athina_mysql_port = os.environ.get("ATHINA_MYSQL_PORT", None)
        self.athina_mysql_username = os.environ.get("ATHINA_MYSQL_USERNAME", None)
        self.athina_mysql_password = os.environ.get("ATHINA_MYSQL_PASSWORD", None)
