package com.codepulse.demo.controller;

import com.codepulse.demo.model.Order;
import com.codepulse.demo.service.OrderService;

public class OrderController {
    private final OrderService orderService;

    public OrderController(OrderService orderService) {
        this.orderService = orderService;
    }

    public String createOrder(Order order) {
        return orderService.placeOrder(order);
    }
}
