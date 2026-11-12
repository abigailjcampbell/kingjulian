start:
    bash command: docker compose up --build
seed data:
    docker compose exec web python manage.py seed_demo
    docker compose exec web python manage.py seed_maintenance
    docker compose exec web python manage.py seed_planner  

test dummy login:
    Log in as owner@sunrise.test (password demo-pass-12345). 
    
    The other seeded logins are viewer@sunrise.test, owner@lakeside.test, and both@demo.test, which belongs to both accounts and shows the account switcher. To create your own admin user, run docker compose exec web python manage.py createsuperuser.


Day-to-day commands:

See code changes:	Just save the file. The project folder is mounted into the container, so the dev server reloads.
See reminder emails:	docker compose logs -f web. In dev, emails print to the console.
Run the tests:	docker compose run --rm web sh -c "pip install -q -r requirements-dev.txt && python manage.py test"
Stop it:	docker compose down
Start from scratch:  docker compose down -v (this also deletes the database and uploaded files)
