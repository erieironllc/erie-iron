from erieiron_common.models import Business


def do_work(business_id):
    business = Business.objects.get(id=business_id)
    business.get_sandbox_dir()
