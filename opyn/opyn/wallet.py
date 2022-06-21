#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ----------------------------------------------------------------------------
# Created By: Anil@Opyn
# Created Date: 06/08/2022
# version ='0.1.0'
# ---------------------------------------------------------------------------
""" Module for wallet utilities """
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------
import eth_keys
import requests
from dataclasses import asdict
from opyn.encode import TypedDataEncoder
from opyn.definitions import  Domain, MessageToSign, OrderData, ContractConfig
from opyn.erc20 import ERC20Contract
from opyn.settlement import SettlementContract
from opyn.utils import hex_zero_pad, get_address

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MESSAGE_TYPES = {
    "OpynRfq": [
        {"name": "bidId", "type": "uint256"},
        {"name": "trader", "type": "address"},
        {"name": "token", "type": "address"},
        {"name": "amount", "type": "uint256"},
        {"name": "nonce", "type": "uint256"},
    ]
}
MIN_ALLOWANCE = 100000000


# ---------------------------------------------------------------------------
# Wallet Instance
# ---------------------------------------------------------------------------
class Wallet:
    """
    Object to generate bid signature

    Args:
        public_key (str): Public key of the user in hex format with 0x prefix
        private_key (str): Private key of the user in hex format with 0x prefix

    Attributes:
        signer (object): Instance of signer to generate signature
    """

    def __init__(self, public_key: str = None, private_key: str = None, relayer_url: str = None, relayer_token: str = None):
        if not private_key and not public_key:
            raise ValueError("Can't instanciate a Wallet without a public or private key")

        self.private_key = private_key
        self.public_key = public_key
        self.relayer_url = relayer_url
        self.relayer_token = relayer_token

        if self.private_key:
            self.signer = eth_keys.keys.PrivateKey(bytes.fromhex(self.private_key[2:]))
            if not self.public_key:
                self.public_key = get_address(self.signer.public_key.to_address())

    def sign_msg(self, messageHash: str) -> dict:
        """Sign a hash message using the signer object

        Args:
            messageHash (str): Message to signed in hex format with 0x prefix

        Returns:
            signature (dict): Signature split into v, r, s components
        """
        signature = self.signer.sign_msg_hash(bytes.fromhex(messageHash[2:]))

        return {
            "v": signature.v + 27,
            "r": hex_zero_pad(hex(signature.r), 32),
            "s": hex_zero_pad(hex(signature.s), 32)
        }

    def _sign_type_data_v4(self, domain: Domain, types: dict, value: dict ) -> dict:
        """Sign a hash of typed data V4 which follows EIP712 convention:
        https://eips.ethereum.org/EIPS/eip-712

        Args:
            domain (dict): Dictionary containing domain parameters including
              name, version, chainId, verifyingContract and salt (optional)
            types (dict): Dictionary of types and their fields
            value (dict): Dictionary of values for each field in types

        Raises:
            TypeError: Domain argument is not an instance of Domain class

        Returns:
            signature (dict): Signature split into v, r, s components
        """
        if not isinstance(domain, Domain):
            raise TypeError("Invalid domain parameters")

        domain_dict = {k: v for k, v in asdict(domain).items() if v is not None}

        return self.sign_msg(TypedDataEncoder._hash(domain_dict, types, value))

    def sign_order_data(self, domain: Domain, message_to_sign: MessageToSign) -> OrderData:
        """Sign a bid using _sign_type_data_v4

        Args:
            domain (dict): Dictionary containing domain parameters including
              name, version, chainId, verifyingContract and salt (optional)
            unsigned_order (UnsignedOrderData): Unsigned Order Data

        Raises:
            TypeError: unsigned_order argument is not an instance of UnsignedOrderData class

        Returns:
            signedBid (dict): Bid combined with the generated signature
        """
        if not isinstance(message_to_sign, MessageToSign):
            raise TypeError("Invalid message_to_sign(MessageToSign)")

        if not self.private_key:
            raise ValueError("Unable to sign. Create the Wallet with the private key argument.")

        signerWallet = get_address(message_to_sign.trader)

        if signerWallet != self.public_key:
            raise ValueError("Signer wallet address mismatch")

        signature = self._sign_type_data_v4(domain, MESSAGE_TYPES, asdict(message_to_sign))

        return OrderData(
            bidId=message_to_sign.bidId,
            trader=message_to_sign.trader,
            token=message_to_sign.token,
            amount=message_to_sign.amount,
            v=signature["v"],
            r=signature["r"],
            s=signature["s"],
        )

    def verify_allowance(self, settlement_config: ContractConfig, token_address: str) -> bool:
        """Verify wallet's allowance for a given token

        Args:
            config (ContractConfig): Configuration to setup the Swap Contract
            token_address (str): Address of token

        Returns:
            verified (bool): True if wallet has sufficient allowance
        """
        token_config = ContractConfig(
            address=token_address,
            rpc_uri=settlement_config.rpc_uri,
            chain_id=settlement_config.chain_id,
        )
        token = ERC20Contract(token_config)

        allowance = (
            token.get_allowance(self.public_key, settlement_config.address)
            / token.decimals
        )

        return allowance > MIN_ALLOWANCE

    def allow_more(self, settlement_config: ContractConfig, token_address: str, amount: str): 
        """Increase settlement contract allowance

        Args:
            settlement_config (ContractConfig): Configuration to setup the Settlement contract
            token_address (str): Address of token to increase allowance of
            amount (str): Amount to increase allowance to
        """
        token_config = ContractConfig(
            address=token_address,
            rpc_uri=settlement_config.rpc_uri,
            chain_id=settlement_config.chain_id,
        )
        token = ERC20Contract(token_config)

        token.approve(self.public_key, self.private_key, settlement_config.address, amount)

    def get_offer_details(self, auction_id: str = None) -> dict : 
        """Get offer details by auction ID

        Args:
            auction_id (str): auction ID

        Returns:
            details (dict): offer details
        """
        response = requests.get(self.relayer_url + "auction/" + auction_id).json()

        return {
            'auctionId': response.auctionId,
            'status': response.status,
        }

    def settle_trade(self, auction_id: str, settlement_config: ContractConfig, bid_order: OrderData):
        """Settle trade

        Args:
            auction_id (str): auction ID
            settlement_config (ContractConfig): Configuration to setup the Settlement contract
            bid_order
        """
        settlement = SettlementContract(settlement_config)

        response = requests.get(self.relayer_url + "/sdk/signature/auction/" + auction_id + "/bid/" + str(bid_order.bidId), headers={'Opyn-Cedefi-Bridge-X-API-Key': self.relayer_token}).json()
        print(response)

        seller_order = OrderData(
            response.bidId,
            response.trader,
            response.amount,
            response.v,
            response.r,
            response.s,
        )

        settlement.settleRfq(self.public_key, self.private_key, bid_order, seller_order)
