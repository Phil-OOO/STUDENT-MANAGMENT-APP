from flask_login import UserMixin


class User(UserMixin):
    def __init__(self, row):
        self.id = row["id"]
        self.school_id = row["school_id"]
        self.name = row["name"]
        self.email = row["email"]
        self.role = row["role"]
        self.school_name = row["school_name"]
        self.school_logo = row["school_logo"]

    def is_admin(self):
        return self.role == "admin"

    def is_superadmin(self):
        return self.role == "superadmin"
