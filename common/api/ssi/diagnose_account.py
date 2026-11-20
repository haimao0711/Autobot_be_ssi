"""In dữ liệu THÔ SSI trả về cho vị thế / số dư / sổ lệnh để đối chiếu tên trường.

Chạy:
  docker exec -it <container-api> python manage.py shell -c "from common.api.ssi.diagnose_account import dump; dump(3)"
"""
import json
from datetime import datetime

from common.api.ssi import client as ssi_client
from common.api.ssi.constants import (
    ENDPOINT_TRADING_POSITION, ENDPOINT_TRADING_ORDER_BOOK,
)


def _show(title, res):
    print(f"\n===== {title}")
    print(json.dumps(res, ensure_ascii=False, indent=2)[:4000])


def dump(user_id):
    from apps.authencation.user.models import User
    from apps.account.detail.services import AccountService

    user = User.objects.get(id=user_id)
    account = AccountService.get_account_by_user(user)
    no = account.account_num

    _show("position", ssi_client.send_request(account, "GET", ENDPOINT_TRADING_POSITION, params={"accountNo": no}))
    _show("position querySummary=true", ssi_client.send_request(
        account, "GET", ENDPOINT_TRADING_POSITION, params={"accountNo": no, "querySummary": "true"}))

    today = datetime.now()
    formats = {
        "yyyy/MM/dd (đang dùng)": (today.strftime("%Y/%m/%d"), today.strftime("%Y/%m/%d")),
        "ISO 8601 (theo tài liệu REST)": (today.strftime("%Y-%m-%dT00:00:00Z"), today.strftime("%Y-%m-%dT23:59:59Z")),
    }
    for name, (f, t) in formats.items():
        _show(f"orderBook {name}", ssi_client.send_request(
            account, "GET", ENDPOINT_TRADING_ORDER_BOOK,
            params={"accountNo": no, "from": f, "to": t, "pageSize": 100, "pageIndex": 1}))
