package com.codepulse.demo.service;

import com.codepulse.demo.model.Order;

public class OrderService {
    private final InventoryService inventoryService;
    private final PaymentService paymentService;
    private final NotificationService notificationService;

    public OrderService(
            InventoryService inventoryService,
            PaymentService paymentService,
            NotificationService notificationService) {
        this.inventoryService = inventoryService;
        this.paymentService = paymentService;
        this.notificationService = notificationService;
    }

    public String placeOrder(Order order) {
        if (!inventoryService.hasStock(order)) {
            return "OUT_OF_STOCK";
        }

        if (!paymentService.processPayment(order)) {
            return "PAYMENT_FAILED";
        }

        inventoryService.reduceStock(order);
        notificationService.sendConfirmation(order);
        return "ORDER_PLACED";
    }
}
