"""Console script for pathfinding_service."""
from gevent import monkey  # isort:skip # noqa
monkey.patch_all()  # isort:skip # noqa

import json
import os
import sys

import click
import structlog
from eth_account import Account
from eth_utils import is_checksum_address
from requests.exceptions import ConnectionError
from web3 import HTTPProvider, Web3
from web3.middleware import geth_poa_middleware

from pathfinding_service import PathfindingService
from pathfinding_service.api.rest import ServiceApi
from pathfinding_service.config import DEFAULT_API_HOST, DEFAULT_POLL_INTERVALL
from pathfinding_service.middleware import http_retry_with_backoff_middleware
from raiden_contracts.constants import CONTRACT_TOKEN_NETWORK_REGISTRY
from raiden_contracts.contract_manager import (
    ContractManager,
    contracts_precompiled_path,
    get_contracts_deployed,
)
from raiden_libs.logging import setup_logging
from raiden_libs.types import Address

log = structlog.get_logger(__name__)
contract_manager = ContractManager(contracts_precompiled_path())

DEFAULT_REQUIRED_CONFIRMATIONS = 8  # ~2min with 15s blocks


def validate_address(ctx, param, value):
    if value is None:
        # None as default value allowed
        return None
    if not is_checksum_address(value):
        raise click.BadParameter('not an EIP-55 checksummed address')
    return value


def get_default_registry_and_start_block(
    net_version: int,
    contracts_version: str,
):
    try:
        contract_data = get_contracts_deployed(net_version, contracts_version)
        token_network_registry_info = contract_data['contracts'][CONTRACT_TOKEN_NETWORK_REGISTRY]
        registry_address = token_network_registry_info['address']
        start_block = max(0, token_network_registry_info['block_number'] - 100)
        return registry_address, start_block
    except ValueError:
        log.error('No deployed contracts were found at the default registry')
        sys.exit(1)


@click.command()
@click.option(
    '--keystore-file',
    required=True,
    type=click.Path(exists=True, dir_okay=False, readable=True),
    help='Path to a keystore file.',
)
@click.password_option(
    '--password',
    help='Password to unlock the keystore file.',
)
@click.option(
    '--eth-rpc',
    default='http://localhost:8545',
    type=str,
    help='Ethereum node RPC URI',
)
@click.option(
    '--registry-address',
    type=str,
    help='Address of the token network registry',
    callback=validate_address,
)
@click.option(
    '--start-block',
    default=0,
    type=click.IntRange(min=0),
    help='Block to start syncing at',
)
@click.option(
    '--confirmations',
    default=DEFAULT_REQUIRED_CONFIRMATIONS,
    type=click.IntRange(min=0),
    help='Number of block confirmations to wait for',
)
@click.option(
    '--host',
    default=DEFAULT_API_HOST,
    type=str,
    help='The host to use for serving the REST API',
)
@click.option(
    '--log-level',
    default='INFO',
    type=click.Choice(['CRITICAL', 'ERROR', 'WARNING', 'INFO', 'DEBUG']),
    help='Print log messages of this level and more important ones',
)
@click.option(
    '--state-db',
    default=os.path.join(click.get_app_dir('raiden-monitoring-service'), 'state.db'),
    type=str,
    help='Path to SQLite3 db which stores the application state',
)
@click.option(
    '--service-fee',
    default=0,
    type=click.IntRange(min=0),
    help='Service fee which is required before processing requests',
)
def main(
    keystore_file: str,
    password: str,
    eth_rpc: str,
    registry_address: Address,
    start_block: int,
    confirmations: int,
    host: str,
    log_level: str,
    state_db: str,
    service_fee: int,
):
    """Console script for pathfinding_service.

    Logging can be quickly set by specifying a global log level or in a
    detailed way by using a log configuration file. See
    https://docs.python.org/3.7/library/logging.config.html#logging-config-dictschema
    for a detailed description of the format.
    """
    setup_logging(log_level)

    log.info("Starting Raiden Pathfinding Service")

    contracts_version = '0.3._'  # FIXME: update this to latest contracts
    log.info(f'Using contracts version: {contracts_version}')

    with open(keystore_file, 'r') as keystore:
        try:
            private_key = Account.decrypt(
                keyfile_json=json.load(keystore),
                password=password,
            ).hex()
        except ValueError as error:
            log.critical(
                'Could not decode keyfile with given password. Please try again.',
                reason=str(error),
            )
            sys.exit(1)
    try:
        log.info(f'Starting Web3 client for node at {eth_rpc}')
        provider = HTTPProvider(eth_rpc)
        web3 = Web3(provider)
        net_version = int(web3.net.version)  # Will throw ConnectionError on bad Ethereum client
    except ConnectionError:
        log.error(
            'Can not connect to the Ethereum client. Please check that it is running and that '
            'your settings are correct.',
        )
        sys.exit(1)

    # Add POA middleware for geth POA chains, no/op for other chains
    web3.middleware_stack.inject(geth_poa_middleware, layer=0)

    # give web3 some time between retries before failing
    provider.middlewares.replace(
        'http_retry_request',
        http_retry_with_backoff_middleware,
    )

    if registry_address is None:
        registry_address, start_block = get_default_registry_and_start_block(
            net_version,
            contracts_version,
        )

    service = None
    api = None
    try:
        log.info('Starting Pathfinding Service...')
        service = PathfindingService(
            web3=web3,
            contract_manager=contract_manager,
            registry_address=registry_address,
            sync_start_block=start_block,
            required_confirmations=confirmations,
            private_key=private_key,
            poll_interval=DEFAULT_POLL_INTERVALL,
            db_filename=state_db,
            service_fee=service_fee,
        )

        api = ServiceApi(service)
        api.run(host=host)

        service.run()
    except (KeyboardInterrupt, SystemExit):
        print('Exiting...')
    finally:
        log.info('Stopping Pathfinding Service...')
        if api:
            api.stop()
        if service:
            service.stop()

    return 0


if __name__ == "__main__":
    main(auto_envvar_prefix='PFS')  # pragma: no cover
