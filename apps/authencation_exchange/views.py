from datetime import datetime
import time as t
import pytz

from django.core.cache import cache

from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.status import HTTP_200_OK, HTTP_400_BAD_REQUEST

from apps.account.user_account.services import UserAccountService
from apps.account.detail.serializers import AccountSerializer
from apps.account.detail.models import Account
from apps.account.detail.enums import AccountLoginStatusEnum, AccountStatusEnum
from apps.account.detail.services import AccountService
# from apps.authencation_exchange.service.smartOne.login import login_smartone_vps, login_get_session_vps_step_1, login_get_session_vps_step_2, VpsBlockedError
from apps.authencation_exchange.service.enums import LoginStatusEnum
from apps.telegram.sender import send_message
from common.signal.enums import SignalTelegramEnum
from apps.telegram.enum.enums import MessageTypeEnum
from common.api.broker import validate_session
from apps.trading.service.handlers import cancel_all_orders
from apps.trading.scheduler.celery_scheduler import create_user_schedules, remove_user_schedules
from apps.configuration.details.overview.services import ConfigurationOverviewServices
from common.errors.messages import ErrorMessages
from apps import api
import logging

logger = logging.getLogger(__name__)

class AuthencationStockExchagesView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        body = request.data

        # Lấy dữ liệu từ request
        otp = body.get("otp")
        is_trading = body.get("isTrading")
        vps_account = AccountService.get_account_by_user(user)
        
        if not vps_account:
            return Response({"data": {"message": ErrorMessages.ACCOUNT_DOES_NOT_EXIST, "status": 0}}, status=HTTP_400_BAD_REQUEST)

        account_name = vps_account.name
        account_num = vps_account.account_num
        pass_login = vps_account.password
        url = api.TRADING_URL
        session_id_old = vps_account.vps_session_id
        session_id = session_id_old if session_id_old else 'stop_trading'
        list_accounts = Account.objects.filter(name=account_name)
        # Hàm cập nhật dữ liệu VPS
        def update_vps_data(session_id, message, status):
            for vps_account in list_accounts:
                vps_data_update = {
                    "vps_session_id": session_id,
                    "login_status": AccountLoginStatusEnum.LoginSuccess.value,
                    "status": AccountStatusEnum.Active.value
                }
                vps_serializer = AccountSerializer(vps_account, data=vps_data_update, partial=True)
                if vps_serializer.is_valid():
                    vps_serializer.save()

            return {
                "message": message,
                "status": status,
                "session_id": session_id
            }
        # Kiểm tra xem có đang giao dịch không
        if is_trading:
            cancel_all_orders(user, account_name, account_num, '', url, session_id, '', 'All')
            result = update_vps_data(session_id, "Trading is stopped!", 2)
            remove_user_schedules(user)  # Dừng scheduler của user (xóa PeriodicTask trong DB)
            ConfigurationOverviewServices.restart_request_trade_overview(user) # Reset is_trading và mua/bán tay
            user.scheduler_status = False
            user.save()
            return Response({"data": result}, status=HTTP_200_OK)

        # SSI FastConnect v3: Xác thực 100% bằng mã SmartOTP / SMS OTP
        from common.api.ssi import client as ssi_client

        # TRƯỜNG HỢP 1: Người dùng đã nhập mã OTP từ giao diện frontend
        if otp:
            otp_code = str(otp).strip()
            try:
                # Xóa token cũ trong cache để lấy token mới hoàn toàn
                ssi_client.clear_writer_token(vps_account)
                token = ssi_client.get_access_token(vps_account, otp=otp_code)
                logger.info("Xác thực OTP SSI thành công từ frontend cho tài khoản %s", account_num)
            except Exception as e:
                logger.error("Xác thực OTP SSI thất bại: %s", e)
                return Response({
                    "data": {
                        "message": f"Mã OTP không đúng hoặc đã hết hạn: {e}",
                        "status": 0,
                        "require_otp": True
                    }
                }, status=HTTP_400_BAD_REQUEST)

            # Token đã xác thực thành công từ SSI và lưu vào Redis
            session_id = 'ssi'
            if session_id != session_id_old:
                ConfigurationOverviewServices.restart_request_trade_overview(user)  # Reset is_trading và mua/bán tay

            # HỦY TẤT CẢ LỆNH CÒN TREO KHI NHẬP LẠI OTP
            try:
                cancel_all_orders(user, account_name, account_num, 'otp_cancel', url, session_id, '', 'All')
                logger.info("Đã dọn dẹp/hủy toàn bộ lệnh treo khi nhập lại OTP cho tài khoản %s", account_num)
            except Exception as e:
                logger.warning("Không thể hủy lệnh treo khi nhập OTP cho %s: %s", account_num, e)

            result = update_vps_data(session_id, "Session hợp lệ!", 1)
            create_user_schedules(user)
            user.scheduler_status = True
            user.save()
            return Response({"data": result}, status=HTTP_200_OK)

        # TRƯỜNG HỢP 2: Người dùng chưa nhập OTP (mới click kết nối hoặc kiểm tra session)
        else:
            # Kiểm tra xem phiên hiện tại có còn hoạt động thực tế trên SSI không
            is_alive = False
            try:
                test_token = ssi_client.cache.get(ssi_client.ACCESS_TOKEN_CACHE_KEY.format(consumer_id=vps_account.ssi_consumer_id))
                if test_token:
                    is_alive, _ = validate_session(account_name, account_num, url, 'ssi', '')
            except Exception:
                is_alive = False

            if is_alive:
                session_id = 'ssi'
                result = update_vps_data(session_id, "Session hợp lệ!", 1)
                create_user_schedules(user)
                user.scheduler_status = True
                user.save()
                return Response({"data": result}, status=HTTP_200_OK)

            # Phiên đã hết hạn hoặc chưa đăng nhập: Xóa sạch token cũ
            ssi_client.clear_writer_token(vps_account)

            # Gửi yêu cầu OTP tới SSI (nếu là tài khoản 2FA SMS hoặc push notification iBoard)
            otp_sent = False
            try:
                ssi_client.request_otp(vps_account)
                otp_sent = True
            except Exception as e:
                logger.warning("request_otp SSI: %s", e)

            msg = (
                "Đã gửi yêu cầu OTP tới SSI, vui lòng nhập mã OTP để tiếp tục!"
                if otp_sent
                else "Phiên SSI đã hết hạn. Vui lòng nhập mã SmartOTP từ app SSI iBoard!"
            )
            return Response({
                "data": {
                    "message": msg,
                    "status": 0,
                    "require_otp": True
                }
            }, status=HTTP_400_BAD_REQUEST)


class AutomationLogoutStockExchangesView(APIView):
    permission_classes = [IsAuthenticated]

    @staticmethod
    def logout_smartone():
        print("Logout all account in smartOne vps has status login is LoginSuccess...")
        _ = AccountService.update_all_accounts_login_status()

    def post(self, requests):
        logout_status = AccountService.update_all_accounts_login_status()
        return Response({
            "data": {
                "message": "Update is successfully"
            }
        }, status=status.HTTP_200_OK)


class AutomationNotifyUserLoginView(APIView):
    permission_classes = [IsAuthenticated]

    @staticmethod
    def notify_login(user):
        accounts = AccountService.get_all_accounts()
        for account in accounts:
            if account.name == '361027':
                if account.login_status != AccountLoginStatusEnum.LoginSuccess.value:
                    notify_login = {
                        'user_account': account.name,
                        'platform_trading': 'SSI' if account.broker == 'ssi' else 'Smart One'
                    }
                    send_message(user, MessageTypeEnum.OVERALL, SignalTelegramEnum.NOTIFY_LOGIN.value, **notify_login)

    @staticmethod
    def notify_running(user):
        vietnam_tz = pytz.timezone('Asia/Ho_Chi_Minh')
        vietnam_time = datetime.now(vietnam_tz)
        time = vietnam_time.strftime('%Y-%m-%d %H:%M:%S')
        notify_running = {
            'time': time
        }

        send_message(user, MessageTypeEnum.OVERALL, SignalTelegramEnum.NOTIFY_RUNNING.value, **notify_running)
