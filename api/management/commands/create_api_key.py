from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from api.models import ApiKey


class Command(BaseCommand):
    help = "Create an API key that acts as the given user, and print it once."

    def add_arguments(self, parser):
        parser.add_argument("name", help="Which system will use the key, e.g. 'ERP sync'")
        parser.add_argument("--user", required=True, help="Username the key acts as")

    def handle(self, *args, **options):
        user = get_user_model().objects.filter(username=options["user"]).first()
        if user is None:
            raise CommandError(f"No user called {options['user']}")
        _, raw_key = ApiKey.issue(options["name"], user)
        self.stdout.write("API key (shown once, store it safely):")
        self.stdout.write(raw_key)
