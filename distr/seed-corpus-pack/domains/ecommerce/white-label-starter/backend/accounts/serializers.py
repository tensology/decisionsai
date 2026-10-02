from rest_framework import serializers
from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from .models import User, Customer, Address

class RegisterSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)
    name = serializers.CharField(max_length=200)
    username = serializers.CharField(required=False, allow_blank=True)

    def validate_password(self, value):
        validate_password(value)
        return value

    def validate_email(self, value):
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("Email already registered.")
        return value.lower()

    def create(self, validated):
        username = validated.get("username") or validated["email"].split("@")[0]
        base = username
        i = 1
        while User.objects.filter(username=username).exists():
            username = f"{base}{i}"
            i += 1
        user = User.objects.create_user(
            username=username,
            email=validated["email"],
            password=validated["password"],
        )
        token = user.issue_activation_token()
        Customer.objects.create(user=user, name=validated["name"], email=validated["email"])
        from mailer.services import send_welcome_activate
        send_welcome_activate(user, token)
        return user


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)

    def validate(self, data):
        user = authenticate(
            request=self.context.get("request"),
            username=data["email"],  # USERNAME_FIELD is email but ModelBackend uses username kw
            password=data["password"],
        )
        # AbstractUser authenticate expects username=USERNAME_FIELD value when using custom user
        if user is None:
            try:
                u = User.objects.get(email__iexact=data["email"])
            except User.DoesNotExist:
                raise serializers.ValidationError("Invalid credentials.")
            if not u.check_password(data["password"]):
                raise serializers.ValidationError("Invalid credentials.")
            user = u
        data["user"] = user
        return data


class ProfileSerializer(serializers.ModelSerializer):
    name = serializers.CharField(source="customer.name", required=False)
    phone = serializers.CharField(source="customer.phone", required=False, allow_blank=True)
    email_verified = serializers.BooleanField(read_only=True)

    class Meta:
        model = User
        fields = ("id", "email", "username", "name", "phone", "email_verified")
        read_only_fields = ("id", "email", "username", "email_verified")

    def update(self, instance, validated):
        customer_data = validated.pop("customer", {})
        instance = super().update(instance, validated)
        if hasattr(instance, "customer") and customer_data:
            for k, v in customer_data.items():
                setattr(instance.customer, k, v)
            instance.customer.save()
        return instance


class AddressSerializer(serializers.ModelSerializer):
    class Meta:
        model = Address
        fields = "__all__"
        read_only_fields = ("customer",)


class PasswordChangeSerializer(serializers.Serializer):
    current_password = serializers.CharField()
    new_password = serializers.CharField()

    def validate_new_password(self, value):
        validate_password(value)
        return value


class ForgotPasswordSerializer(serializers.Serializer):
    email = serializers.EmailField()


class ResetPasswordSerializer(serializers.Serializer):
    token = serializers.CharField()
    new_password = serializers.CharField()

    def validate_new_password(self, value):
        validate_password(value)
        return value


class EmailChangeSerializer(serializers.Serializer):
    new_email = serializers.EmailField()
