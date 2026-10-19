package com.codepulse.demo.service;

import com.codepulse.demo.model.Order;

public class NotificationService {
    public void sendConfirmation(Order order) {
        System.out.println("Confirmation sent for " + order.getOrderId());
    }
}
