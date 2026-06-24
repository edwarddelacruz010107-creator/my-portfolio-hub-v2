import psycopg2

conn = psycopg2.connect(
    "postgresql://core_db_63xe_user:MsaWPyREwZdRby5AHDBruS9KNFcwuB9C@dpg-d8oa1lj7uimc739c9p20-a.oregon-postgres.render.com/core_db_63xe"
)

print("connected")