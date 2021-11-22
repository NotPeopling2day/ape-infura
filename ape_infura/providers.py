import os

from ape.api import ReceiptAPI, TransactionAPI, Web3Provider
from ape.exceptions import ContractLogicError, ProviderError, TransactionError, VirtualMachineError
from ape.utils import gas_estimation_error_message
from web3 import HTTPProvider, Web3  # type: ignore
from web3.exceptions import ContractLogicError as Web3ContractLogicError
from web3.gas_strategies.rpc import rpc_gas_price_strategy
from web3.middleware import geth_poa_middleware

_ENVIRONMENT_VARIABLE_NAMES = ("WEB3_INFURA_PROJECT_ID", "WEB3_INFURA_API_KEY")


class InfuraProviderError(ProviderError):
    """
    An error raised by the Infura provider plugin.
    """


class MissingProjectKeyError(InfuraProviderError):
    def __init__(self):
        env_var_str = ", ".join([f"${n}" for n in _ENVIRONMENT_VARIABLE_NAMES])
        super().__init__(f"Must set one of {env_var_str}")


class Infura(Web3Provider):
    def connect(self):
        key = None
        for env_var_name in _ENVIRONMENT_VARIABLE_NAMES:
            env_var = os.environ.get(env_var_name)
            if env_var:
                key = env_var
                break

        if not key:
            raise MissingProjectKeyError()

        self._web3 = Web3(HTTPProvider(f"https://{self.network.name}.infura.io/v3/{key}"))
        if self._web3.eth.chain_id in (4, 5, 42):
            self._web3.middleware_onion.inject(geth_poa_middleware, layer=0)
        self._web3.eth.set_gas_price_strategy(rpc_gas_price_strategy)

    def disconnect(self):
        self._web3 = None  # type: ignore

    def estimate_gas_cost(self, txn: TransactionAPI) -> int:
        """
        Generates and returns an estimate of how much gas is necessary
        to allow the transaction to complete.
        The transaction will not be added to the blockchain.
        """
        try:
            return super().estimate_gas_cost(txn)
        except ValueError as err:
            tx_error = _get_vm_error(err)

            # If this is the cause of a would-be revert,
            # raise ContractLogicError so that we can confirm tx-reverts.
            if isinstance(tx_error, ContractLogicError):
                raise tx_error from err

            message = gas_estimation_error_message(tx_error)
            raise TransactionError(base_err=tx_error, message=message) from err

    def send_transaction(self, txn: TransactionAPI) -> ReceiptAPI:
        """
        Creates a new message call transaction or a contract creation
        for signed transactions.
        """
        try:
            receipt = super().send_transaction(txn)
        except ValueError as err:
            raise _get_vm_error(err) from err

        receipt.raise_for_status(txn)
        return receipt


def _get_vm_error(web3_err: ValueError) -> VirtualMachineError:
    if not hasattr(web3_err, "args") or not len(web3_err.args):
        return VirtualMachineError(base_err=web3_err)

    args = web3_err.args
    message = args[0]
    if (
        not isinstance(web3_err, Web3ContractLogicError)
        and isinstance(message, dict)
        and "message" in message
    ):
        # Is some other VM error, like gas related
        return VirtualMachineError(message=message["message"])

    elif not isinstance(message, str):
        return VirtualMachineError(base_err=web3_err)

    # If get here, we have detected a contract logic related revert.
    message_prefix = "execution reverted"
    if message.startswith(message_prefix):
        message = message.replace(message_prefix, "")

        if ":" in message:
            # Was given a revert message
            message = message.split(":")[-1].strip()
            return ContractLogicError(revert_message=message)
        else:
            # No revert message
            # TODO: Won't have to specify `revert_message=""` once 0.1.0a28
            return ContractLogicError(revert_message="")

    return VirtualMachineError(message=message)