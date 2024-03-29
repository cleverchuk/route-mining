#!/usr/bin/python
# -*- coding: utf-8 -*-
from io import BytesIO
import json
import os
from typing import Deque, List
from collections import deque
from urllib import request

from lib.file_io import default_file_io_factory

from lib.address import Address, AddressBuilder, AddressEncoder
from web.file_upload import SESSION_KEY


CARRIER_ROUTE_ENDPOINT = "https://tools.usps.com/tools/app/ziplookup/zipByAddress"

ADDRESS_LOOKUP = "https://geocode.arcgis.com/arcgis/rest/services/World/GeocodeServer/findAddressCandidates"

HEADERS = {
    "user-agent": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko) Chrome/97.0.0.71 Safari/537.0"
}


class Responder:
    """
        Interface for responders.
        This class enable the implementation of intercepting filter pattern
    """

    def respond(self, addresses: List[Address]) -> List[Address]:
        raise NotImplementedError


class AddressValidationResponder(Responder):
    """
        A responder that validates each address
    """

    def __init__(self, api_client: request):
        self.api_client: request = api_client

    def respond(self, addresses: List[Address]) -> List[Address]:
        """
            Validates addresses by calling an API to get the best match address
        """
        for idx in range(len(addresses)):
            address = addresses[idx]
            # prepare params for API call
            if address.apt_number:
                addr = f"{address.street_number} {address.street_name} apt #{address.apt_number} {address.city} {address.state} {address.zip}"
            else:
                addr = f"{address.street_number} {address.street_name} {address.city} {address.state} {address.zip}"

            params = {
                "SingleLine": addr,
                "f": "json",
                "outFields": "*",
                "countryCode": "US"
            }
            # make API call
            resp_json = self.api_client.get(
                ADDRESS_LOOKUP, headers=HEADERS, params=params).json()
            # extract candidate addresses
            candidates = resp_json["candidates"]
            # grab the address with the best score
            address_json = candidates[0] if len(candidates) else ""
            # validate address
            verdict, new_address = self.validate(address, address_json)

            if not verdict:  # update the address if they don't match
                addresses[idx] = new_address

        return addresses

    def validate(self, address: Address, address_json: dict) -> tuple:
        """
            Check the user provide address is the same as the best match from api call
        """
        if not address_json:  # if there's no data, we assume the provided address is correct since there's nothing we can do about it other filtering the address out
            return (True, address)

        # extract relevant information from the response from the API call
        address_attributes = address_json["attributes"]
        street_number = address_attributes["AddNum"]
        street_name = f"{address_attributes['StName']} {address_attributes['StType']}"

        apt_number = address_attributes["SubAddr"]
        city = address_attributes["City"]
        state = address_attributes["RegionAbbr"]
        zip = address_attributes["Postal"]

        # create new address from the information and compare with the given address
        received_address = AddressBuilder()\
            .street_number(street_number)\
            .street_name(street_name)\
            .apt_number(apt_number)\
            .city(city)\
            .state(state)\
            .zip(zip)\
            .build()

        return (address == received_address, received_address)


class CarrierRouteRetreiverResponder(Responder):
    """
        A responder that retrieves the carrier route for each address
    """

    def __init__(self, api_client: request):
        self.api_client: request = api_client

    def respond(self, addresses: List[Address]) -> List[Address]:
        for address in addresses:
            # prepare data for API call
            data = {
                "address1": f"{address.street_number} {address.street_name}",
                "address2": f"{address.apt_number}",
                "city": f"{address.city}",
                "state": f"{address.state}",
                "urbanCode": "",
                "zip": f"{address.zip}"
            }
            # make API
            resp_json = self.api_client.post(
                CARRIER_ROUTE_ENDPOINT, headers=HEADERS, data=data).json()
            # extract the address list
            address_list = resp_json["addressList"]

            for address_json in address_list:
                if address_json["carrierRoute"]:  # grab the first available carrier route
                    # update carrier route
                    address.carrier_route = address_json["carrierRoute"]
                    break

        return addresses


class ReportGeneratorResponder(Responder):
    """
        A responder that stores the addresses plus the carrier route to
        the file system
    """

    def __init__(self, filename: str = "data.json"):
        self.filename = filename

    def respond(self, addresses: List[Address]) -> List[Address]:
        """
            Write addresses plus carrier route to the file store
        """
        from flask import current_app, session
        json_string = json.dumps(addresses, cls=AddressEncoder)
        bytes_io = BytesIO(json_string.encode("utf-8"))

        file_path = os.path.join(
            current_app.config['UPLOAD_FOLDER'], session[SESSION_KEY] + self.filename)
        file_io = default_file_io_factory.create(current_app.env)
        file_io.write(bytes_io, file_path)

        return addresses


class ResponderPipeline(Responder):
    """
        A list of Responders which intercepts and processes addresses
    """

    def __init__(self):
        self.responders: Deque[Responder] = deque()

    def add_first(self, responder: Responder) -> None:
        """
            Add a responder to front of the list
        """
        self.responders.appendleft(responder)

    def add_last(self, responder: Responder) -> None:
        """
            Add a responder to back of the list
        """
        self.responders.append(responder)

    def respond(self, addresses: List[Address]) -> List[Address]:
        """
            Executes the responder pipeline
        """
        for responder in self.responders:
            response = responder.respond(addresses)

        return response
