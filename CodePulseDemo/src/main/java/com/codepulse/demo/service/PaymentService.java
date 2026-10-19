package com.codepulse.demo.service;

import com.codepulse.demo.model.Order;

public class PaymentService {
    public boolean processPayment(Order order) {
        System.out.println("Processing payment: " + order.getAmount());
        return order.getAmount() >= 100;
    }
}

