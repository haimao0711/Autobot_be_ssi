import logging
from django.db import transaction

logger = logging.getLogger(__name__)

from common.errors.messages import ErrorMessages
from common.success.types import SuccessType
from common.errors.types import ErrorType
from common.calculator import calculate_profit, calculate_stoploss

from apps.stock.services import StockService, DownloadService
from apps.stock.models import StockM1, StockD1
from apps.configuration.candle.enums import CandleEnum
from apps.stock.enums import DownloadStatusEnum

from apps.account.detail.services import AccountService
from apps.configuration.details.serializers import ConfigurationSerializers, TradeHandleSerializers
from apps.configuration.candle.services import CandleService
from apps.account.user_account.services import UserAccountService
from apps.configuration.details.services import ConfigurationServices
from apps.trading.services import TradingService
from apps.trading.enum.enums import StatusPidEnum
from apps.trading.serializers import TradingSerializer
# from apps.balance.models import SubAccountBalance
# from apps.balance.serializers import SubAccountBalanceSerializer
# from apps.balance.services import BalanceService
from ..template.services import ConfigurationTemplateServices
from ...details.models import Configuration
from ...type.services import ConfigurationTypeServices
from concurrent.futures import ThreadPoolExecutor
from apps.authencation.user.models import User
from apps.account.detail.models import Account

from apps.stockSocket.services.cache_service import get_price
from apps import api
import json

class ConfigurationOverviewServices:
  
    def get_detail_stock_configuration(user, stock_id):
        overview = ConfigurationTypeServices.get_overview()

        try:
            configurations = Configuration.objects.get(
                user=user, config_type=overview.id, stock_id=stock_id)
            return ConfigurationSerializers(configurations).data

        except Configuration.DoesNotExist:
            return {}

    @staticmethod
    def get_stock_balance(user):
        """Lấy danh mục cổ phiếu thực tế từ sàn giao dịch (SSI/VPS) của user."""
        vps_account = AccountService.get_account_by_user(user)
        if not vps_account:
            return []
        stock_balance_data = []
        try:
            from common.api.ssi import handler as ssi_handler
            positions = ssi_handler._get_positions(vps_account)
            for p in positions:
                m = ssi_handler._position_metrics(p)
                symbol = ssi_handler._first(p, 'symbol', 'instrumentID', 'stock_name', 'shareCode')
                if not symbol:
                    continue
                if m['quantity'] <= 0 and m['sellable'] <= 0 and m['avg_price'] <= 0:
                    continue
                stock_balance_data.append({
                    'symbol': str(symbol).strip().upper(),
                    'actual_vol': int(m['quantity']),
                    'avaiable_vol': int(m['sellable']),
                    'avg_price': ssi_handler._from_vnd(m['avg_price']),
                    'gain_loss_per': round(m['gain_loss_pct'], 2),
                    'market_price': ssi_handler._from_vnd(m['price']),
                })
        except Exception as e:
            logger.error(f"Error get_stock_balance for user {user.id}: {e}")
        return stock_balance_data

    @staticmethod
    def invalidate_user_config_cache(user_id):
        """Xóa cache danh sách cấu hình của user khi có thay đổi."""
        try:
            from common.api.ssi.client import cache as ssi_cache
            ssi_cache.delete(f"cache:config_stock:{user_id}")
        except Exception:
            pass

    def get_detail_stock_configurations(user):
        from common.api.ssi.client import cache as ssi_cache
        cache_key = f"cache:config_stock:{user.id}"
        cached_result = ssi_cache.get(cache_key)
        if cached_result:
            try:
                return json.loads(cached_result)
            except Exception:
                pass

        overview = ConfigurationTypeServices.get_overview()
        try:
            configurations = Configuration.objects.filter(
                user=user, config_type=overview.id
            ).select_related( 'stock', 'config_type' )
            stock_ids = [config.stock.id for config in configurations]

            # Cache giá nến M1 mới nhất trong 10 giây để tránh scan toàn bộ bảng StockM1
            cache_key_m1 = "cache:m1_latest_prices"
            query_data_m1_all = None
            cached_m1 = ssi_cache.get(cache_key_m1)
            if cached_m1:
                try:
                    query_data_m1_all = json.loads(cached_m1)
                except Exception:
                    query_data_m1_all = None
            if not query_data_m1_all:
                query_data_m1_all = StockService.get_last_stock_chart_items_by_time_bulk(
                    StockModel=StockM1, stock_ids=stock_ids
                )
                try:
                    ssi_cache.set(cache_key_m1, json.dumps(query_data_m1_all), timeout=10)
                except Exception:
                    pass

            query_data_m1_map = {item['id']: item for item in query_data_m1_all}

            following_type = ConfigurationTypeServices.get_following()
            trading_type = ConfigurationTypeServices.get_trading()
            following_configs = Configuration.objects.filter(
                user=user,
                config_type=following_type.id,
                stock_id__in=stock_ids
            ).select_related('candle', 'candle_sell', 'stock')
            trading_configs = Configuration.objects.filter(
                user=user,
                config_type=trading_type.id,
                stock_id__in=stock_ids
            ).select_related('candle', 'candle_sell', 'stock')

            following_config_map = {cfg.stock.id: cfg for cfg in following_configs}
            trading_config_map = {cfg.stock.id: cfg for cfg in trading_configs}

            # Chuẩn bị stock_data
            stock_data = {}
            for config in configurations:
                stock_id = config.stock.id
                stock_name = config.stock.name
                query_data_m1 = query_data_m1_map.get(stock_id)           
                # Lấy trading chart từ map
                trading_cfg = trading_config_map.get(stock_id)
                trading_chart_buy = trading_cfg.candle.candle if trading_cfg and trading_cfg.candle else ''
                trading_chart_sell = trading_cfg.candle_sell.candle_sell if trading_cfg and trading_cfg.candle_sell else ''

                # Lấy following chart từ map
                following_cfg = following_config_map.get(stock_id)
                following_chart_buy = following_cfg.candle.candle if following_cfg and following_cfg.candle else ''
                following_chart_sell = following_cfg.candle_sell.candle_sell if following_cfg and following_cfg.candle_sell else ''

                # Xong thêm phản hồi chart
                low_price = query_data_m1['low'] if query_data_m1 else 0
                high_price = query_data_m1['high'] if query_data_m1 else 0
                average_price = (float(low_price) + float(high_price)) / 2
                current_price = query_data_m1['close'] if query_data_m1 else 0

                stock_data[stock_name] = {
                    'id': stock_id,
                    'name': stock_name,
                    'low': low_price,
                    'high': high_price,
                    'close': current_price,
                    'average_price': average_price,
                    'following_chart_buy': following_chart_buy,
                    'following_chart_sell' : following_chart_sell,
                    'trading_chart_buy' : trading_chart_buy,
                    'trading_chart_sell' : trading_chart_sell,
                }
            vps_account = AccountService.get_account_by_user(user)
            stock_balance_data = []
            account_name = getattr(vps_account, 'name', '')
            account_num = getattr(vps_account, 'account_num', '')

            if vps_account:
                # Cache vị thế danh mục tài khoản trong Redis 15 giây để không gửi HTTP tới SSI mỗi 10s
                cache_key_pos = f"cache:position:{getattr(vps_account, 'broker', 'smartone')}:{account_num}"
                cached_pos = ssi_cache.get(cache_key_pos)
                if cached_pos:
                    try:
                        stock_balance_data = json.loads(cached_pos)
                    except Exception:
                        stock_balance_data = []

                if not stock_balance_data:
                    try:
                        # Dùng chung logic với handle_stock_balance_service để số liệu nhất quán
                        from common.api.ssi import handler as ssi_handler
                        positions = ssi_handler._get_positions(vps_account)
                        for p in positions:
                            m = ssi_handler._position_metrics(p)
                            symbol = ssi_handler._first(p, 'symbol', 'instrumentID', 'stock_name', 'shareCode')
                            if not symbol:
                                continue
                            if m['quantity'] <= 0 and m['sellable'] <= 0 and m['avg_price'] <= 0:
                                continue
                            stock_balance_data.append({
                                'symbol': str(symbol).strip().upper(),
                                'actual_vol': int(m['quantity']),
                                'avaiable_vol': int(m['sellable']),
                                'avg_price': ssi_handler._from_vnd(m['avg_price']),
                                'gain_loss_per': round(m['gain_loss_pct'], 2),
                                'market_price': ssi_handler._from_vnd(m['price']),
                            })
                        if stock_balance_data:
                            ssi_cache.set(cache_key_pos, json.dumps(stock_balance_data), timeout=15)
                    except Exception as e:
                        logger.error(f"SSI stock balance error in config: {e}")

            # Xử lý đồng thời
            def process_configuration(configuration):
                stock_name = configuration.stock.name
                current_price = stock_data[stock_name]['close']
                following_chart_buy = stock_data[stock_name]['following_chart_buy']
                following_chart_sell = stock_data[stock_name]['following_chart_sell']
                trading_chart_buy = stock_data[stock_name]['trading_chart_buy']
                trading_chart_sell = stock_data[stock_name]['trading_chart_sell']
                stock_name_clean = str(stock_name or '').strip().upper()
                balance_by_stock = next((stock for stock in stock_balance_data if str(stock.get('symbol') or '').strip().upper() == stock_name_clean), None)
                return {
                    'account_vps': account_name,
                    'account_vps_num': account_num,
                    'id': configuration.id,
                    'is_block_buy': configuration.is_block_buy,
                    'is_block_sell': configuration.is_block_sell,
                    'is_buy_hand': configuration.is_buy_hand,
                    'is_sell_hand': configuration.is_sell_hand,
                    'is_use_price_to_buy': configuration.is_use_price_to_buy,
                    'price_to_buy_now': configuration.price_to_buy_now if configuration.price_to_buy_now is not None else 0.0,
                    'is_use_stoploss': configuration.is_use_stoploss,
                    'is_use_takeprofit': configuration.is_use_takeprofit,
                    'is_update_volume_buy': configuration.is_update_volume_buy,
                    'volume_to_buy': configuration.volume_to_buy if configuration.volume_to_buy is not None else 0.0,
                    'stock_id': configuration.stock.id,
                    'stock_name': configuration.stock.name,
                    'current_price': current_price,
                    'status_pid': 'FOLLOWING',
                    'volume_buy': balance_by_stock.get('actual_vol', 0) if balance_by_stock else 0,
                    'volume_trade': balance_by_stock.get('avaiable_vol', 0) if balance_by_stock else 0,
                    'aver_price_buy': balance_by_stock.get('avg_price', 0) if balance_by_stock else 0,
                    'current_profit': balance_by_stock.get('gain_loss_per', 0) if balance_by_stock else 0,
                    "chart_type": configuration.config_type.name,
                    "level": configuration.level,
                    'following_chart_buy': following_chart_buy,
                    'following_chart_sell' : following_chart_sell,
                    'trading_chart_buy' : trading_chart_buy,
                    'trading_chart_sell' : trading_chart_sell,
                }

            # List comprehension thuần Python cực nhanh (<1ms) thay vì ThreadPoolExecutor
            data = [process_configuration(config) for config in configurations]

            # Bổ sung các cổ phiếu ĐANG NẮM GIỮ nhưng chưa có cấu hình overview để frontend hiển thị
            configured_symbols = {str(c.stock.name).strip().upper() for c in configurations if c.stock and c.stock.name}
            for held in stock_balance_data:
                held_symbol = str(held.get('symbol') or '').strip().upper()
                if not held_symbol or held_symbol in configured_symbols:
                    continue
                held_stock = None
                try:
                    held_stock = StockService.get_stock_by_symbol(held_symbol)
                except Exception:
                    held_stock = None

                # Lấy cấu hình Overview đã lưu nếu có (để giữ price_to_buy_now, volume_to_buy)
                existing_cfg = None
                if held_stock:
                    existing_cfg = Configuration.objects.filter(
                        user=user, stock=held_stock, config_type=overview.id
                    ).first()

                data.append({
                    'account_vps': account_name,
                    'account_vps_num': account_num,
                    'id': getattr(existing_cfg, 'id', None),
                    'is_configured': bool(existing_cfg),
                    'is_block_buy': getattr(existing_cfg, 'is_block_buy', False),
                    'is_block_sell': getattr(existing_cfg, 'is_block_sell', False),
                    'is_buy_hand': getattr(existing_cfg, 'is_buy_hand', False),
                    'is_sell_hand': getattr(existing_cfg, 'is_sell_hand', False),
                    'is_use_price_to_buy': getattr(existing_cfg, 'is_use_price_to_buy', False),
                    'price_to_buy_now': getattr(existing_cfg, 'price_to_buy_now', 0.0) or 0.0,
                    'is_use_stoploss': getattr(existing_cfg, 'is_use_stoploss', False),
                    'is_use_takeprofit': getattr(existing_cfg, 'is_use_takeprofit', False),
                    'is_update_volume_buy': getattr(existing_cfg, 'is_update_volume_buy', False),
                    'volume_to_buy': getattr(existing_cfg, 'volume_to_buy', 0.0) or 0.0,
                    'stock_id': getattr(held_stock, 'id', None),
                    'stock_name': held_symbol,
                    'current_price': held.get('market_price', 0),
                    'status_pid': 'FOLLOWING',
                    'volume_buy': held.get('actual_vol', 0),
                    'volume_trade': held.get('avaiable_vol', 0),
                    'aver_price_buy': held.get('avg_price', 0),
                    'current_profit': held.get('gain_loss_per', 0),
                    'chart_type': '',
                    'level': getattr(existing_cfg, 'level', 0) if existing_cfg else 0,
                    'following_chart_buy': '',
                    'following_chart_sell': '',
                    'trading_chart_buy': '',
                    'trading_chart_sell': '',
                })

            # Cache toàn bộ kết quả cho user trong 8 giây (phù hợp chu kỳ polling 10s)
            try:
                ssi_cache.set(cache_key, json.dumps(data), timeout=8)
            except Exception:
                pass

            return data
            # sorted_data = sorted(data, key=lambda x: int(x["volume_buy"]), reverse=True)
            # print('Kết thúc hàm get_detail_stock_configurations')
            return data

        except Exception as error:
            print('error', str(error))
            return []

    def create_a_new_overview_configuration(user, init_overview_data):
        stock = StockService.get_stock_by_id(stock_id=init_overview_data.get('stock_id'))
        print('check stock is create: ', stock)
        following = ConfigurationTypeServices.get_following()
        trading = ConfigurationTypeServices.get_trading()
        
        following_candle = CandleService.get_candle(
            candle_type=init_overview_data.get('chart_following'))
        trading_candle = CandleService.get_candle(
            candle_type=init_overview_data.get('chart_trading'))
        
        template_data = ConfigurationTemplateServices.get_base_configuration_template(user=user)
        template_data['user'] = user.id
        template_data['stock'] = stock.id
        
        following_data = template_data.copy()
        following_data['config_type'] = following.id
        following_data['candle'] = following_candle.id
        
        trading_data = template_data.copy()
        trading_data['config_type'] = trading.id
        trading_data['candle'] = trading_candle.id
        
        return_data = []
        
        # Kiểm tra xem cấu hình đã tồn tại chưa
        if ConfigurationOverviewServices.check_is_overview_exist(user=user, stock_id=stock.id):
            overview_configuration = ConfigurationOverviewServices.get_detail_stock_configuration(user=user, stock_id=stock.id)
            return_data.append(overview_configuration)
        else:
            with transaction.atomic():
                init_data = {
                    'user': user.id,
                    'stock': stock.id,
                    'config_type': ConfigurationTypeServices.get_overview().id,
                    'is_use_price_to_buy': bool(init_overview_data.get('is_use_price_to_buy', False)),
                    'price_to_buy_now': float(init_overview_data.get('price_to_buy_now') or 0.0),
                    'is_use_stoploss': bool(init_overview_data.get('is_use_stoploss', False)),
                    'percent_to_stoploss': float(init_overview_data.get('percent_to_stoploss') or 0.0),
                    'is_use_takeprofit': bool(init_overview_data.get('is_use_takeprofit', False)),
                    'percent_to_takeprofit': float(init_overview_data.get('percent_to_takeprofit') or 0.0),
                    'is_update_volume_buy': bool(init_overview_data.get('is_update_volume_buy', False)),
                    'volume_to_buy': float(init_overview_data.get('volume_to_buy') or 0.0)
                }
                
                overview_serializer = ConfigurationSerializers(data=init_data)
                if overview_serializer.is_valid():
                    overview = overview_serializer.save()
                else:
                    raise ValueError(str(overview_serializer.errors))
                
                StockService.download_imported_new_data_to_database_chart(stock_ids=[stock.id])
                
                # Tạo cấu hình following và trading nếu chưa tồn tại
                if not ConfigurationServices.is_exist_configuration(user=user, stock_id=stock.id):
                    following_serializer = ConfigurationSerializers(data=following_data)
                    if following_serializer.is_valid():
                        following_serializer.save()
                    else:
                        raise ValueError(str(following_serializer.errors))
                    
                    trading_serializer = ConfigurationSerializers(data=trading_data)
                    if trading_serializer.is_valid():
                        trading_serializer.save()
                    else:
                        raise ValueError(str(trading_serializer.errors))
                    
                return_data.append(init_data)
        
        ConfigurationOverviewServices.invalidate_user_config_cache(user.id)
        return SuccessType.CREATE_SUCCESS, return_data


    def create_overview_configuration(user, init_overview_data):
        if ConfigurationTemplateServices.check_template_is_exist(user=user):
            return ConfigurationOverviewServices.create_a_new_overview_configuration(user, init_overview_data)
        return ErrorType.CREATE_FAILED, {}

    def update_a_overview_configuration(user, update_overview_data):
        allow_fields = ['is_update_volume_buy', 'is_use_price_to_buy', 'is_block_buy', 'is_block_sell', 'is_buy_hand', 'is_sell_hand',
                        'is_use_stoploss', 'is_use_takeprofit', 'price_to_buy_now', 'volume_to_buy', 'margin_percentage', 'level']
        update_data = {
            field: value
            for field in allow_fields
            if (value := update_overview_data.get(field)) is not None
        }

        config_id = update_overview_data.get('id')
        stock_id = update_overview_data.get('stock_id')
        stock_name = update_overview_data.get('stock_name')
        overview_type = ConfigurationTypeServices.get_overview()

        overview_configuration = None
        if config_id:
            overview_configuration = Configuration.objects.filter(id=config_id, user=user).first()
        if not overview_configuration and stock_id:
            overview_configuration = Configuration.objects.filter(user=user, config_type=overview_type.id, stock_id=stock_id).first()
        if not overview_configuration and stock_name:
            overview_configuration = Configuration.objects.filter(user=user, config_type=overview_type.id, stock__name__iexact=str(stock_name).strip()).first()

        # Nếu chưa có trong DB (ví dụ từ danh sách cổ phiếu đang nắm giữ), tự động tạo bản ghi Overview để lưu cấu hình
        if not overview_configuration:
            target_stock = None
            if stock_id:
                target_stock = StockService.get_stock_by_id(stock_id)
            elif stock_name:
                target_stock = StockService.get_stock_by_symbol(str(stock_name).strip())

            if target_stock:
                try:
                    overview_configuration = Configuration.objects.create(
                        user=user,
                        stock=target_stock,
                        config_type=overview_type,
                        price_to_buy_now=float(update_overview_data.get('price_to_buy_now') or 0.0),
                        volume_to_buy=float(update_overview_data.get('volume_to_buy') or 0.0),
                        is_use_price_to_buy=bool(update_overview_data.get('is_use_price_to_buy', False)),
                        is_update_volume_buy=bool(update_overview_data.get('is_update_volume_buy', False)),
                    )
                except Exception as e:
                    logger.error(f"Tạo overview configuration tự động thất bại: {e}")

        if not overview_configuration:
            return ErrorType.UPDATE_FAILED, {'message': 'Không tìm thấy cấu hình mã cổ phiếu để cập nhật.'}

        try:
            overview_serializer = TradeHandleSerializers(
                overview_configuration, data=update_data, partial=True)
            if overview_serializer.is_valid():
                updated_instance = overview_serializer.save()
                return_data = {field: getattr(
                    updated_instance, field) for field in allow_fields}
                return_data['user'] = user.id
                return_data['stock_id'] = getattr(updated_instance.stock, 'id', stock_id)
                ConfigurationOverviewServices.invalidate_user_config_cache(user.id)
                return SuccessType.UPDATED_SUCCESS, return_data
            else:
                print("Serializer errors:", overview_serializer.errors)
                return ErrorType.UPDATE_FAILED, {
                    'message': str(overview_serializer.errors)
                }
        except Exception as errors:
            return ErrorType.UPDATE_FAILED, {
                'message': str(errors)
            }

    def update_overview_configuration(user, update_overview_data):
        if ConfigurationTemplateServices.check_template_is_exist(user=user):
            return ConfigurationOverviewServices.update_a_overview_configuration(user, update_overview_data)
        return ErrorType.UPDATE_FAILED, {
            'message': 'this case'
        }

    def update_block_trade_overview(user, type_block, is_block_buy, is_block_sell):
        overview_type = ConfigurationTypeServices.get_overview()

        # Lọc danh sách các bản ghi cần cập nhật
        overviews = Configuration.objects.filter(user=user, config_type=overview_type.id)

        if overviews.exists():
            # Cập nhật toàn bộ các bản ghi
            if type_block == 'B':
                overviews.update(is_block_buy=is_block_buy)
            elif type_block == 'S':
                overviews.update(is_block_sell=is_block_sell)
            ConfigurationOverviewServices.invalidate_user_config_cache(user.id)
            return overviews  # Trả về danh sách các bản ghi đã được cập nhật

        return {}  # Không có bản ghi nào để cập nhật
    def restart_request_trade_overview(user):
        # Trả lại trạng thái ban đầu của mua bán tay và is_trading của cấu hình
        overview_type = ConfigurationTypeServices.get_overview()
        trading_type = ConfigurationTypeServices.get_trading()
        # Lọc danh sách các bản ghi cần cập nhật
        overviews = Configuration.objects.filter(user=user, config_type=overview_type.id)
        tradings = Configuration.objects.filter(user=user, config_type=trading_type.id)
        if overviews.exists():
            overviews.update(is_buy_hand=False, is_sell_hand=False)
        if tradings.exists():
            tradings.update(is_trading=False)
        ConfigurationOverviewServices.invalidate_user_config_cache(user.id)
        return {}  # Không có bản ghi nào để cập nhật

    def restart_single_stock_trade_status(user, stock_symbol):
        # Reset trạng thái cho 1 mã cụ thể
        from apps.stock.models import Stock
        try:
            stock = Stock.objects.get(name=stock_symbol)
            overview_type = ConfigurationTypeServices.get_overview()
            trading_type = ConfigurationTypeServices.get_trading()
            
            Configuration.objects.filter(user=user, stock=stock, config_type=overview_type.id).update(is_buy_hand=False, is_sell_hand=False)
            Configuration.objects.filter(user=user, stock=stock, config_type=trading_type.id).update(is_trading=False)
            ConfigurationOverviewServices.invalidate_user_config_cache(user.id)
            return True
        except Exception as e:
            print(f"Error resetting status for {stock_symbol}: {e}")
            return False
    
    def restart_is_trading_configutation(user):
        trading_type = ConfigurationTypeServices.get_trading()

        # Lọc danh sách các bản ghi cần cập nhật
        tradings = Configuration.objects.filter(user=user, config_type=trading_type.id)

        if tradings.exists():
            tradings.update(is_trading=False)

        return {}  # Không có bản ghi nào để cập nhật

    def count_overview_configurations(user):
        overview = ConfigurationTypeServices.get_overview()

        try:
            configurations = Configuration.objects.filter(
                user=user, config_type=overview.id)
            return len(configurations)
        except Exception:
            return 0

    def count_overview_configuration(user, stock_id):
        overview = ConfigurationTypeServices.get_overview()

        try:
            configurations = Configuration.objects.filter(
                user=user, config_type=overview.id, stock=stock_id)
            return len(configurations)
        except Exception:
            return 0

    def delete_overview_configuration(user, delete_overview_data):
        stock_id = delete_overview_data.get('stock_id')

        num_of_overview = ConfigurationOverviewServices.count_overview_configuration(
            user=user, stock_id=stock_id
        )
        overview_configuration = ConfigurationOverviewServices.get_overview_exist(
            user=user, stock_id=stock_id
        )

        if num_of_overview == 0:
            return SuccessType.DELETE_SUCCESS, {}

        elif num_of_overview == 1:
            with transaction.atomic():
                trading_configuration = ConfigurationServices.get_trading_configuration(
                    user=user, stock_id=stock_id
                )
                following_configuration = ConfigurationServices.get_following_configuration(
                    user=user, stock_id=stock_id
                )

                if trading_configuration is not None:
                    trading_configuration.delete()
                if following_configuration is not None:
                    following_configuration.delete()
                    
                overview_configuration.delete()

        else:
            overview_configuration.delete()

        ConfigurationOverviewServices.invalidate_user_config_cache(user.id)
        return SuccessType.DELETE_SUCCESS, {}

    def check_is_overview_exist(user, stock_id):
        overview = ConfigurationTypeServices.get_overview()

        try:
            _ = Configuration.objects.get(
                user=user, config_type=overview.id, stock=stock_id)
            return True
        except Configuration.DoesNotExist:
            return False

    def get_overview_exist(user, stock_id):
        overview_type = ConfigurationTypeServices.get_overview()

        try:
            overview = Configuration.objects.get(
                user=user, config_type=overview_type.id, stock=stock_id)
            return overview
        except Configuration.DoesNotExist:
            return {}
        
    def get_all_overview(user):
        overview_type = ConfigurationTypeServices.get_overview()

        try:
            overview = Configuration.objects.get(
                user=user, config_type=overview_type.id)
            return overview
        except Configuration.DoesNotExist:
            return {}

    def handle_convert_data(template_data):
        stoploss_field_to_extract = ['stock_config_use_stop_loss_first_part', 'stock_config_percent_stop_loss_sell_first', 'stock_config_use_stop_loss_trigger',
                                     'stock_config_stop_loss_percent', 'stock_config_use_stop_loss_second_part', 'stock_config_percent_stop_loss_sell_second']
        takeprofit_field_to_extract = ['stock_config_use_take_profit_first_part', 'stock_config_percent_take_profit_sell_first', 'stock_config_use_take_profit_trigger',
                                       'stock_config_take_profit_percent', 'stock_config_use_take_profit_second_part', 'stock_config_percent_take_profit_sell_second']
        stoploss_obj = {field: template_data.get(
            field) for field in stoploss_field_to_extract}
        takeprofit_obj = {field: template_data.get(
            field) for field in takeprofit_field_to_extract}
        is_use_stoploss, percent_stoploss = calculate_stoploss(
            stoploss_obj=stoploss_obj)
        is_use_profit, percent_profit = calculate_profit(
            profit_obj=takeprofit_obj)
        percent_to_stoploss = round(
            1 - percent_stoploss, 2) if percent_stoploss <= 1 else 0
        percent_to_takeprofit = round(
            percent_profit - 1, 2) if percent_profit >= 1 else round(percent_profit, 2)
        percent_to_stoploss = percent_to_stoploss if is_use_stoploss else 0
        percent_to_takeprofit = percent_to_takeprofit if is_use_profit else 0

        return {
            'is_use_stoploss': is_use_stoploss,
            'percent_to_stoploss': percent_to_stoploss,
            'is_use_profit': is_use_profit,
            'percent_to_takeprofit': percent_to_takeprofit
        }

    @staticmethod
    def calculate_profit_using(configuration: Configuration):
        takeprofit_obj = {
            'stock_config_use_take_profit_first_part': configuration.stock_config_use_take_profit_first_part,
            'stock_config_percent_take_profit_sell_first': configuration.stock_config_percent_take_profit_sell_first,
            'stock_config_use_take_profit_trigger': configuration.stock_config_use_take_profit_trigger,
            'stock_config_take_profit_percent': configuration.stock_config_take_profit_percent,
            'stock_config_use_take_profit_second_part': configuration.stock_config_use_take_profit_second_part,
            'stock_config_percent_take_profit_sell_second': configuration.stock_config_percent_take_profit_sell_second
        }
        is_use_profit, percent_profit = calculate_profit(
            profit_obj=takeprofit_obj)

        return is_use_profit, percent_profit

    @staticmethod
    def calculate_stoploss_using(configuration: Configuration):
        stoploss_obj = {
            'stock_config_use_stop_loss_first_part': configuration.stock_config_use_stop_loss_first_part,
            'stock_config_percent_stop_loss_sell_first': configuration.stock_config_percent_stop_loss_sell_first,
            'stock_config_use_stop_loss_trigger': configuration.stock_config_use_stop_loss_trigger,
            'stock_config_stop_loss_percent': configuration.stock_config_stop_loss_percent,
            'stock_config_use_stop_loss_second_part': configuration.stock_config_use_stop_loss_second_part,
            'stock_config_percent_stop_loss_sell_second': configuration.stock_config_percent_stop_loss_sell_second
        }
        is_use_stl, percent_stl = calculate_stoploss(
            stoploss_obj=stoploss_obj)

        return is_use_stl, percent_stl
