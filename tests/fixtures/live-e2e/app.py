import sqlite3

def login(request):
    username = request.args.get("username")
    query = "SELECT * FROM users WHERE username = '%s'" % username
    conn = sqlite3.connect('db.sqlite3')
    return conn.execute(query).fetchall()
