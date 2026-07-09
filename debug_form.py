#!/usr/bin/env python3
from app import create_app
from app.forms import LandingContactForm

app = create_app()
with app.app_context():
    form = LandingContactForm()
    # Render the full_name field
    html = str(form.full_name())
    print("=== Full Name Field HTML ===")
    print(html)
    print("\n=== All form fields ===")
    for field in form:
        if field.type != 'CSRFTokenField' and field.name != 'submit':
            print(f"Field name: {field.name}, Type: {field.type}")
            print(f"  HTML: {str(field())[:150]}\n")

