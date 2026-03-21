from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.contrib.auth.models import User


class AppSignupForm(UserCreationForm):
    class Meta:
        model = User
        fields = ("username", "email")


class AppLoginForm(AuthenticationForm):
    pass
