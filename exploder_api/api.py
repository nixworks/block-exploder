import connexion
import sys
import os
import ConfigParser
from flask_cors import CORS
from gateways import DatabaseGateway
from pymongo import MongoClient
from serializers import TransactionSerializer, BlockSerializer, HashrateSerializer, \
    NetworkStatsSerializer, SyncHistorySerializer, ClientInfoSerializer, PriceSerializer, \
    SearchSerializer, TransactoinCountSerializer, VolumeSerializer, \
    BalanceSerializer
from bitcoinrpc.authproxy import AuthServiceProxy, JSONRPCException
from helpers import validate_address, validate_sha256_hash

######################
#  INITIALIZE STUFF  #
######################
CONFIG_FILE = os.environ['EXPLODER_CONFIG']
config = ConfigParser.RawConfigParser()
config.read(CONFIG_FILE)

rpc_user = config.get('syncer', 'rpc_user')
rpc_password = config.get('syncer', 'rpc_password')
rpc_port = config.getint('syncer', 'rpc_port')

mongo = MongoClient()
db = DatabaseGateway(database=mongo.exploder, config=config)
rpc = AuthServiceProxy("http://%s:%s@127.0.0.1:%s"
                       % (rpc_user, rpc_password, rpc_port))


############
#  BLOCKS  #
############
def get_latest_blocks(limit, offset):
    if not isinstance(offset, int):
        return "Offset too large", 400
    blocks = db.get_latest_blocks(limit, offset)
    return [BlockSerializer.to_web(block) for block in blocks]


def get_block_by_hash(block_hash):
    if not validate_sha256_hash(block_hash):
        return "Invalid block hash", 400
    try:
        return BlockSerializer.to_web(db.get_block_by_hash(block_hash))
    except KeyError:
        return "Block with given hash doesn't exist", 404


def get_block_by_height(height):
    # If we don't check whether height is integer
    # MongoDB will throw OverflowError
    # because MongoDB can only handle up to 8-byte ints
    if not isinstance(height, int):
        return "Block height too large", 400
    try:
        return BlockSerializer.to_web(db.get_block_by_height(height))
    except KeyError:
        return "Block with given height doesn't exist", 404


def get_block_confirmations(block_hash):
    if not validate_sha256_hash(block_hash):
        return "Invalid block hash", 400
    try:
        block = db.get_block_by_hash(block_hash)
    except KeyError:
        return "Block with given hash doesn't exist", 404

    return {
        "hash": block_hash,
        "confirmations": db.calculate_block_confirmations(block)
    }


##################
#  TRANSACTIONS  #
##################
def get_transaction_by_txid(txid):
    if not validate_sha256_hash(txid):
        return "Invalid transaction ID", 400
    try:
        return TransactionSerializer.to_web(db.get_transaction_by_txid(txid))
    except KeyError:
        return "Transaction with given ID not found", 404


def get_transaction_confirmations(txid):
    if not validate_sha256_hash(txid):
        return "Invalid transaction ID", 400
    try:
        tr = db.get_transaction_by_txid(txid)
    except KeyError:
        return "Transaction with given txid not found", 404

    block = db.get_block_by_hash(tr['blockhash'])
    return {
        "txid": txid,
        "confirmations": db.calculate_block_confirmations(block)
    }


def get_latest_transactions(limit, offset):
    if not isinstance(offset, int):
        return "Offset too large", 400
    transactions = db.get_latest_transactions(limit, offset)
    return [TransactionSerializer.to_web(tr) for tr in transactions]


def get_transactions_by_blockhash(blockhash):
    if not validate_sha256_hash(blockhash):
        return "Invalid block hash", 400
    try:
        return [TransactionSerializer.to_web(tr) for tr in db.get_transactions_by_blockhash(blockhash)]
    except KeyError:
        return []


###############
#  ADDRESSES  #
###############
def get_address_transactions(address_hash, start=None):
    if start and (not isinstance(start, int)):
        return "Start too large", 400
    if not validate_address(address_hash):
        return "Invalid address hash", 400
    trs = db.get_address_transactions(address_hash, start, limit=51)

    if len(trs) == 51:
        last_transaction = trs[len(trs) - 1]
        return {
            "transactions": [TransactionSerializer.to_web(tr) for tr in trs],
            "next": "/addresses/%s?start=%s" % (address_hash, last_transaction['blocktime'])
        }
    else:
        return {
            "transactions": [TransactionSerializer.to_web(tr) for tr in trs],
            "next": None
        }


def get_address_num_transactions(address_hash):
    if not validate_address(address_hash):
        return "Invalid address hash", 400
    tr_count = db.get_address_num_transactions(address_hash)
    return TransactoinCountSerializer.to_web(address_hash, tr_count)


def get_address_volume(address_hash):
    if not validate_address(address_hash):
        return "Invalid address hash", 400
    volume = db.get_address_volume(address_hash)
    return VolumeSerializer.to_web(address_hash, volume)


def get_address_unspent(address_hash):
    if not validate_address(address_hash):
        return "Invalid address hash", 400
    unspent = db.get_address_unspent(address_hash)
    return unspent


def get_address_balance(address_hash):
    if not validate_address(address_hash):
        return "Invalid address hash", 400
    balance = db.get_address_balance(address_hash)
    return BalanceSerializer.to_web(address_hash, balance)


#############
#  NETWORK  #
#############
def send_raw_transaction(hex):
    try:
        txid = rpc.sendrawtransaction(hex)
        return {
            "txid": txid
        }
    except JSONRPCException as e:
        return e.error, 400


def get_latest_hashrates(limit):
    hash_rates = db.get_latest_hashrates(limit)
    return [HashrateSerializer.to_web(hash_rate) for hash_rate in hash_rates]


def get_network_stats():
    hash_rate = db.get_latest_hashrates(limit=1)
    stats = db.get_network_stats()
    block_count = db.get_block_count(config.get('syncer', 'main_chain'))
    tr_count = db.get_transaction_count()
    return NetworkStatsSerializer.to_web(stats, hash_rate[0], block_count, tr_count)


def get_bootstrap_link():
    bootstrap_dir = config.get('syncer', 'bootstrap_dir')

    bootstrap_path = os.path.join(bootstrap_dir, 'bootstrap.dat')
    if not os.path.isfile(bootstrap_path):
        return "Bootstrap.dat doesn't exist on the server", 404

    generated = os.stat(bootstrap_path).st_ctime
    bootstrap_server_path = os.path.join(
        config.get('syncer', 'bootstrap_dir_server_path'),
        'bootstrap.zip'
    )

    return {
        "url": bootstrap_server_path,
        "generated": generated
    }


def get_usd_price():
    stats = db.get_network_stats()
    return PriceSerializer.to_web(stats["usd_price"])


##############
#   CLIENT   #
##############
def get_latest_sync_history(limit, offset):
    if not isinstance(offset, int):
        return "Offset too large", 400
    sync_history = db.get_latest_sync_history(limit, offset)
    return [SyncHistorySerializer.to_web(history) for history in sync_history]


def get_client_info():
    client_info = db.get_client_info()
    return ClientInfoSerializer.to_web(client_info)


############
#  SEARCH  #
############
def search(search_param):
    param_type = db.search(search_param)
    return SearchSerializer.to_web(search_param, param_type)


def create_and_run_app(port=5000):
    """
    Runs a new instance of a tornado server listening on the given port
    """
    api = connexion.App(__name__)
    api.add_api('explorer_api.yaml')
    CORS(api.app)
    api.run(server='tornado', port=port)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("USAGE: python api.py [PORT]")
        sys.exit(1)

    create_and_run_app(int(sys.argv[1]))
