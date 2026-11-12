from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('account', '0013_account_limit_total_market_value'),
    ]

    operations = [
        migrations.AddField(
            model_name='account',
            name='broker',
            field=models.CharField(choices=[('smartone', 'SmartOne (VPS)'), ('ssi', 'SSI FastConnect')], default='smartone', max_length=20),
        ),
        migrations.AddField(
            model_name='account',
            name='ssi_consumer_id',
            field=models.CharField(blank=True, max_length=100, null=True),
        ),
        migrations.AddField(
            model_name='account',
            name='ssi_consumer_secret',
            field=models.CharField(blank=True, max_length=200, null=True),
        ),
        migrations.AddField(
            model_name='account',
            name='ssi_private_key_path',
            field=models.CharField(blank=True, max_length=300, null=True),
        ),
        migrations.AddField(
            model_name='account',
            name='ssi_two_fa_type',
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name='account',
            name='ssi_pin',
            field=models.CharField(blank=True, max_length=50, null=True),
        ),
    ]

