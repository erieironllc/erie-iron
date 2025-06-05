class CapabilityManager:
    def fetch_or_create_capability(self, name, description):
        try:
            return Capability.objects.get(name=name)
        except Capability.DoesNotExist:
            # Logic to determine whether to build or escalate
            pass