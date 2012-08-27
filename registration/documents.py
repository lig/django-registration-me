import datetime
import random
import re
import sha

from django.conf import settings
from django.template.loader import render_to_string
from mongoengine import StringField
from mongoengine.django.auth import User

from signals import activate

SHA1_RE = re.compile('^[a-f0-9]{40}$')


class RegistrationProfile(User):
    """
    A simple profile which stores an activation key for use during
    user account registration.

    Generally, you will not want to interact directly with instances
    of this model; the provided manager includes methods
    for creating and activating new accounts, as well as for cleaning
    out accounts which have never been activated.

    While it is possible to use this model as the value of the
    ``AUTH_PROFILE_MODULE`` setting, it's not recommended that you do
    so. This model's sole purpose is to store data temporarily during
    account registration and activation, and a mechanism for
    automatically creating an instance of a site-specific profile
    model is provided via the ``create_inactive_user`` on
    ``RegistrationManager``.

    """
    ACTIVATED = u"ALREADY_ACTIVATED"

    activation_key = StringField(max_length=40)

    def __unicode__(self):
        return u"Registration information for %s" % self.username

    def activation_key_expired(self):
        """
        Determine whether this ``RegistrationProfile``'s activation
        key has expired, returning a boolean -- ``True`` if the key
        has expired.

        Key expiration is determined by a two-step process:

        1. If the user has already activated, the key will have been
           reset to the string ``ALREADY_ACTIVATED``. Re-activating is
           not permitted, and so this method returns ``True`` in this
           case.

        2. Otherwise, the date the user signed up is incremented by
           the number of days specified in the setting
           ``ACCOUNT_ACTIVATION_DAYS`` (which should be the number of
           days after signup during which a user is allowed to
           activate their account); if the result is less than or
           equal to the current date, the key has expired and this
           method returns ``True``.

        """
        expiration_date = datetime.timedelta(
            days=settings.ACCOUNT_ACTIVATION_DAYS)
        return self.activation_key == self.ACTIVATED or \
               (self.date_joined + expiration_date <= datetime.datetime.now())
    activation_key_expired.boolean = True

    @classmethod
    def activate_user(cls, activation_key):
        """
        Validate an activation key and activate the corresponding
        ``User`` if valid.

        If the key is valid and has not expired, return the ``User``
        after activating.

        If the key is not valid or has expired, return ``False``.

        If the key is valid but the ``User`` is already active,
        return ``False``.

        To prevent reactivation of an account which has been
        deactivated by site administrators, the activation key is
        reset to the string ``ALREADY_ACTIVATED`` after successful
        activation.

        """
        # Make sure the key we're trying conforms to the pattern of a
        # SHA1 hash; if it doesn't, no point trying to look it up in
        # the database.
        if SHA1_RE.search(activation_key):
            profile = cls.objects(class_check=False,
                activation_key=activation_key).first()
            if profile and not profile.activation_key_expired():
                profile.is_active = True
                profile.activation_key = cls.ACTIVATED
                profile.save()
                activate.send(cls, document=profile)
                return profile
        return False

    @classmethod
    def create_inactive_user(cls, username, password, email, send_email=True):
        """
        Create a new, inactive ``User``, generates a
        ``RegistrationProfile`` and email its activation key to the
        ``User``, returning the new ``User``.

        To disable the email, call with ``send_email=False``.

        The activation email will make use of two templates:

        ``registration/activation_email_subject.txt``
            This template will be used for the subject line of the
            email. It receives one context variable, ``site``, which
            is the currently-active
            ``django.contrib.sites.models.Site`` instance. Because it
            is used as the subject line of an email, this template's
            output **must** be only a single line of text; output
            longer than one line will be forcibly joined into only a
            single line.

        ``registration/activation_email.txt``
            This template will be used for the body of the email. It
            will receive three context variables: ``activation_key``
            will be the user's activation key (for use in constructing
            a URL to activate the account), ``expiration_days`` will
            be the number of days for which the key will be valid and
            ``site`` will be the currently-active
            ``django.contrib.sites.models.Site`` instance.

        To enable creation of a custom user profile along with the
        ``User`` (e.g., the model specified in the
        ``AUTH_PROFILE_MODULE`` setting), define a function which
        knows how to create and save an instance of that model with
        appropriate default values, and pass it as the keyword
        argument ``profile_callback``. This function should accept one
        keyword argument:

        ``user``
            The ``User`` to relate the profile to.

        """
        registration_profile = cls.create_user(username, password, email)
        registration_profile.is_active = False
        registration_profile.save()

        if send_email:
            from django.core.mail import send_mail

            subject = render_to_string(
                'registration/activation_email_subject.txt',
                {'site': settings.SITE})
            # Email subject *must not* contain newlines
            subject = ''.join(subject.splitlines())

            message = render_to_string('registration/activation_email.txt',
                {'activation_key': registration_profile.activation_key,
                    'expiration_days': settings.ACCOUNT_ACTIVATION_DAYS,
                    'site': settings.SITE})

            send_mail(subject, message, settings.DEFAULT_FROM_EMAIL,
                [registration_profile.email])
        return registration_profile

    @classmethod
    def create_user(cls, *args, **kwargs):
        """
        Create a ``RegistrationProfile`` for a given
        ``User``, and return the ``RegistrationProfile``.

        The activation key for the ``RegistrationProfile`` will be a
        SHA1 hash, generated from a combination of the ``User``'s
        username and a random salt.

        """
        profile = super(cls, cls).create_user(*args, **kwargs)
        salt = sha.new(str(random.random())).hexdigest()[:5]
        profile.activation_key = sha.new(salt + profile.username).hexdigest()
        profile.save()
        return profile

    @classmethod
    def delete_expired_users(cls):
        """
        Remove expired instances of ``RegistrationProfile`` and their
        associated ``User``s.

        Accounts to be deleted are identified by searching for
        instances of ``RegistrationProfile`` with expired activation
        keys, and then checking to see if their associated ``User``
        instances have the field ``is_active`` set to ``False``; any
        ``User`` who is both inactive and has an expired activation
        key will be deleted.

        It is recommended that this method be executed regularly as
        part of your routine site maintenance; this application
        provides a custom management command which will call this
        method, accessible as ``manage.py cleanupregistration``.

        Regularly clearing out accounts which have never been
        activated serves two useful purposes:

        1. It alleviates the ocasional need to reset a
           ``RegistrationProfile`` and/or re-send an activation email
           when a user does not receive or does not act upon the
           initial activation email; since the account will be
           deleted, the user will be able to simply re-register and
           receive a new activation key.

        2. It prevents the possibility of a malicious user registering
           one or more accounts and never activating them (thus
           denying the use of those usernames to anyone else); since
           those accounts will be deleted, the usernames will become
           available for use again.

        If you have a troublesome ``User`` and wish to disable their
        account while keeping it in the database, simply delete the
        associated ``RegistrationProfile``; an inactive ``User`` which
        does not have an associated ``RegistrationProfile`` will not
        be deleted.

        """
        for profile in cls.objects():
            if profile.activation_key_expired() and not profile.is_active:
                profile.delete()
