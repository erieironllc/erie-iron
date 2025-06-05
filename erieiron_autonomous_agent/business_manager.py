from models import Business


class BusinessManager:
    def create_business(self, name, goals, shutdown_plan):
        business = Business.objects.create(name=name, shutdown_plan=shutdown_plan)
        business.goals.set(goals)
        return business

    def shutdown_business(self, business: Business):
        # Execute shutdown plan
        pass