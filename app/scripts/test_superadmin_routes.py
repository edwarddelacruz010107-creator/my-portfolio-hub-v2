from app import create_app

app = create_app()

with app.test_client() as c:
    r = c.get('/superadmin/login')
    print('status_code:', r.status_code)
    data = r.get_data(as_text=True)
    print(data[:300].replace('\n',' '))
