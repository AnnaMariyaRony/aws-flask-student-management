import os
import pymysql

try:
    connection = pymysql.connect(
        host=os.environ["DB_HOST"],
        port=int(os.environ["DB_PORT"]),
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        database=os.environ["DB_NAME"],
        connect_timeout=10
    )
    print("SUCCESS: Connected to the RDS database!")
    connection.close()

except Exception as error:
    print("Connection failed:", error)
